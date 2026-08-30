"""Per-``(tenant, subject)`` request rate limiting — ADR 0014.

The key is taken from the *verified* token: the tenant the issuer resolved to
and the ``sub`` claim the middleware put on ``request.state.principal``. It is
never a client address. Behind the Caddy edge and Compose's bridge network
every request arrives from a proxy, so an address-keyed
limiter would either throttle the whole deployment as one caller or force
``--forwarded-allow-ips`` into a security decision. A verified token cannot be
spoofed by a header, which is what makes it the only honest key here.

The bucket is a token bucket rather than a fixed window: a fixed window lets a
caller spend a whole minute's allowance in the last second of one window and
again in the first second of the next, which is the burst the ranking path
cannot absorb. Capacity is ``RATE_LIMIT_BURST``, refill is
``RATE_LIMIT_REQUESTS_PER_MINUTE / 60`` tokens per second.

Two implementations of that arithmetic live here and they are not equivalent:

  * ``SharedTokenBucket`` keeps the bucket in Redis and charges it with one
    atomic Lua script, so every uvicorn worker — and every replica, if there is
    ever more than one — meets the same bucket. This is the default outside a
    dev box, and it is what makes the configured limit mean what it says.
  * ``TokenBucketLimiter`` keeps the bucket in the worker's own memory. It
    stays for two jobs: it is the fallback the shared bucket fails open onto
    when Redis is unreachable, and it is the backend a test or a rig selects
    deliberately with ``RATE_LIMIT_BACKEND=memory``. It is *not* an equivalent
    limiter — an N-worker service running it admits up to N times the
    configured rate in aggregate, and a keep-alive client pinned to one worker
    meets one Nth of it (ADR 0014's 2026-08-27 measurement: 37.9% of one
    subject's 301 canary requests refused at a nominal aggregate ceiling
    of 480/minute). ``Settings`` refuses it outside dev unless the operator
    acknowledges that in writing.

The limits are global unless the tenant overrides them. Migration 0016 put
``rate_limit_requests_per_minute`` and ``rate_limit_burst`` on
``public.tenants``; ``RateLimitMiddleware`` takes an injected resolver for
them, coalesces each column independently against the settings policy, and
passes the result to ``charge`` as that request's ``RateLimitPolicy``. A
deployment that sets neither column behaves exactly as it did before. Because
capacity and refill are arguments to the script rather than properties of a
limiter object, a tenant's quota costs no extra bucket and a changed quota
takes effect on the next request without resetting anyone's bucket — see
``RateLimitMiddleware._policy_for``, which is the one place that answers
"what limit applies to this caller".
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal, Protocol

import redis.asyncio as aioredis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff
from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src.auth.middleware import UNAUTHENTICATED_PATHS
from src.config import Settings
from src.serving.tenancy import TenantRateLimit

logger = logging.getLogger(__name__)

LIMIT_HEADER = "X-RateLimit-Limit"
REMAINING_HEADER = "X-RateLimit-Remaining"
RESET_HEADER = "X-RateLimit-Reset"
RETRY_AFTER_HEADER = "Retry-After"

# Ceiling on distinct live buckets in the in-process map. A bucket is a handful
# of floats plus its key, so this is kilobytes rather than megabytes; the
# ceiling exists so that a deployment facing many subjects cannot turn the
# limiter into an unbounded map. The shared bucket needs no equivalent: Redis
# expires an idle key on its own.
DEFAULT_MAX_BUCKETS = 4_096

# How much longer than one full refill a Redis bucket outlives its last use. A
# key that has been idle for longer than `capacity / refill` has refilled to
# capacity, at which point it is indistinguishable from a key that never
# existed — so expiring it changes no future decision. The slack is there so a
# clock skew or a slow round trip cannot expire a bucket that is still draining.
BUCKET_TTL_SLACK_SECONDS = 60

# Fail-open events are logged at most this often, with the count the line
# stands for. A Redis outage under load would otherwise write one warning per
# request, which is a second incident on top of the first.
FAIL_OPEN_LOG_INTERVAL_SECONDS = 30.0

# How long after a Redis error `/readyz` keeps reporting the limiter degraded.
# Long enough that a blip is visible to a probe that runs every thirty seconds,
# short enough that a recovered service stops advertising an old failure.
DEGRADED_WINDOW_SECONDS = 60.0

RateLimitKey = tuple[str, str]

# How the middleware asks for a tenant's quota (migration 0016). Async because
# the answer comes from the tenant registry, and the caller — ``src.serving.app``
# — is the only thing that knows how to read it without blocking the event loop.
# The second argument is the request's own transaction when there is one, so a
# registry read never asks the pool for a connection while this request holds
# one. It is typed as an opaque object because this module performs no I/O of
# its own beyond the bucket: the limiter forwards the handle without knowing
# what a database connection is.
TenantLimitResolver = Callable[[str, object | None], Awaitable[TenantRateLimit]]

# What `/readyz` reports about the limiter. Reported, never gating: a limiter
# is backpressure, not an auth boundary, so a service whose bucket is degraded
# is still a service that can answer.
RateLimitBackendState = Literal["shared", "in-process", "degraded", "disabled"]


def bucket_key(tenant_id: str, subject: str) -> str:
    """Redis key for one ``(tenant, subject)`` bucket.

    Under the tenant-scoped prefix ``TenantRouter`` already produces for the
    online feature store (``tenant:<id>:``), so a tenant's rate-limit state
    lives in the same namespace as the rest of its Redis state and a
    ``KEYS tenant:demo:*`` sweep finds all of it. The prefix is composed here
    rather than read off a resolved ``TenantConfig`` because the bucket has to
    be chargeable before — and without — a registry lookup: the fail-open path
    charges it while the registry is exactly what could not be read.
    ``tests/unit/test_ratelimit.py`` asserts the two spellings agree so the
    copies cannot drift.
    """
    return f"tenant:{tenant_id}:ratelimit:{subject}"


@dataclass(frozen=True)
class RateLimitPolicy:
    """The two numbers one bucket is charged against.

    A value object rather than two constructor arguments because it is the
    thing that becomes per-tenant when the quota column lands on
    ``public.tenants``: every ``charge`` takes one, and the middleware resolves
    it in exactly one place.
    """

    requests_per_minute: int
    burst: int

    def __post_init__(self) -> None:
        # Refused rather than read as "unlimited" or as "refuse everything":
        # `RATE_LIMIT_REQUESTS_PER_MINUTE=0` is a typo, and the two readings of
        # it are a silent outage and a silent no-op respectively.
        if self.requests_per_minute <= 0:
            raise ValueError("requests_per_minute must be positive")
        if self.burst <= 0:
            raise ValueError("burst must be positive")

    @property
    def capacity(self) -> float:
        return float(self.burst)

    @property
    def refill_per_second(self) -> float:
        return self.requests_per_minute / 60.0

    @property
    def ttl_seconds(self) -> int:
        """How long a Redis bucket outlives its last charge."""
        return math.ceil(self.capacity / self.refill_per_second) + BUCKET_TTL_SLACK_SECONDS

    def describe(self) -> str:
        """One-line policy summary, used in the 429 body and the boot log."""
        return f"{self.requests_per_minute} requests/minute with a burst of {self.burst}"


@dataclass(frozen=True)
class RateLimitDecision:
    """One bucket read, with everything the response headers need.

    ``remaining`` is floored: a partial token cannot serve a request, so
    advertising it would tell a client it has an allowance it does not have.
    ``reset_seconds`` is when the bucket is full again — not when the window
    rolls over, because a token bucket has no window.
    """

    allowed: bool
    limit: int
    remaining: int
    reset_seconds: int
    retry_after_seconds: int


def _decision(*, allowed: bool, tokens: float, policy: RateLimitPolicy) -> RateLimitDecision:
    """Render the headers for a bucket left holding ``tokens``.

    Shared by both backends so the two can never describe the same bucket
    state differently — the in-process one is the fallback the shared one fails
    open onto mid-request, and a client must not be able to tell which answered
    from the numbers alone.
    """
    return RateLimitDecision(
        allowed=allowed,
        limit=policy.burst,
        remaining=int(tokens),
        reset_seconds=math.ceil((policy.capacity - tokens) / policy.refill_per_second),
        # Never advertise 0: a client that reads Retry-After as "sleep this
        # long" would spin. The floor is one whole second.
        retry_after_seconds=(
            0 if allowed else max(1, math.ceil((1.0 - tokens) / policy.refill_per_second))
        ),
    )


class RateLimiter(Protocol):
    """What ``RateLimitMiddleware`` needs from a bucket, whichever holds it."""

    @property
    def policy(self) -> RateLimitPolicy:
        """The policy applied when the caller names none."""

    def describe(self) -> str:
        """One-line summary of the default policy, for the boot log."""

    async def charge(
        self, key: RateLimitKey, policy: RateLimitPolicy | None = None
    ) -> RateLimitDecision:
        """Charge one request against ``key``'s bucket and report the result."""

    async def report(self) -> RateLimitBackendState:
        """What ``/readyz`` should say about this backend. Never raises."""


class TokenBucketLimiter:
    """In-process token buckets keyed by ``(tenant_id, subject)``.

    Every operation is O(1) and touches no I/O — this is the limiter's
    fail-open path, so it has to be able to answer in the middle of a request
    that has already spent its Redis timeout. The eviction sweep is the one
    exception and only runs when the map is over its ceiling.
    """

    def __init__(
        self,
        *,
        requests_per_minute: int,
        burst: int,
        max_buckets: int = DEFAULT_MAX_BUCKETS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_buckets <= 0:
            raise ValueError("max_buckets must be positive")
        self._policy = RateLimitPolicy(requests_per_minute=requests_per_minute, burst=burst)
        self._max_buckets = max_buckets
        # Monotonic by default: a clock that steps backwards over an NTP
        # correction would hand out an unbounded allowance, and one that steps
        # forwards would throttle a caller who did nothing wrong.
        self._clock = clock
        self._lock = threading.Lock()
        # tokens, updated_at, and the policy that bucket was last charged
        # against — the capacity is per bucket rather than per limiter so that
        # a per-tenant policy can arrive without the eviction sweep ranking two
        # different bucket sizes against each other.
        self._buckets: dict[RateLimitKey, tuple[float, float, RateLimitPolicy]] = {}

    @property
    def policy(self) -> RateLimitPolicy:
        return self._policy

    @property
    def requests_per_minute(self) -> int:
        return self._policy.requests_per_minute

    @property
    def burst(self) -> int:
        return self._policy.burst

    @property
    def bucket_count(self) -> int:
        with self._lock:
            return len(self._buckets)

    def describe(self) -> str:
        return self._policy.describe()

    async def charge(
        self, key: RateLimitKey, policy: RateLimitPolicy | None = None
    ) -> RateLimitDecision:
        return self.acquire(key, policy)

    async def report(self) -> RateLimitBackendState:
        return "in-process"

    def acquire(
        self, key: RateLimitKey, policy: RateLimitPolicy | None = None
    ) -> RateLimitDecision:
        """Charge one request against ``key``'s bucket and report the result.

        Synchronous as well as awaitable through ``charge`` because the shared
        bucket calls it from an exception handler, where there is nothing left
        to await on.
        """
        effective = policy or self._policy
        now = self._clock()
        with self._lock:
            tokens, updated_at, _ = self._buckets.get(key, (effective.capacity, now, effective))
            elapsed = max(0.0, now - updated_at)
            tokens = min(effective.capacity, tokens + elapsed * effective.refill_per_second)
            allowed = tokens >= 1.0
            if allowed:
                tokens -= 1.0
            self._buckets[key] = (tokens, now, effective)
            if len(self._buckets) > self._max_buckets:
                self._evict(now)
        return _decision(allowed=allowed, tokens=tokens, policy=effective)

    def _evict(self, now: float) -> None:
        """Drop the fullest buckets until the map is back under its ceiling.

        A bucket that has refilled to capacity is indistinguishable from one
        that was never created, so dropping it changes no future decision at
        all — those go first and are usually the whole sweep. When every live
        bucket is mid-recovery the next-fullest goes, because the caller with
        the most headroom is the one who loses the least by being forgotten.
        Fullness is a fraction of that bucket's own capacity, so two tenants on
        different quotas are ranked on how recovered they are rather than on
        whose quota is larger.

        The obvious alternative, least-recently-used, is wrong here: the least
        recently *seen* bucket is typically the caller who was just refused and
        backed off, so LRU would reach the throttled caller first and hand them
        a fresh allowance — turning the ceiling into a way around the limit.
        """
        ranked = sorted(
            (
                (-self._refilled_fraction(tokens, updated_at, policy, now), key)
                for key, (tokens, updated_at, policy) in self._buckets.items()
            ),
        )
        for _, key in ranked:
            if len(self._buckets) <= self._max_buckets:
                break
            del self._buckets[key]

    def _refilled_fraction(
        self, tokens: float, updated_at: float, policy: RateLimitPolicy, now: float
    ) -> float:
        refilled = min(
            policy.capacity,
            tokens + max(0.0, now - updated_at) * policy.refill_per_second,
        )
        return refilled / policy.capacity


# One round trip, one decision. The whole point of the script is that the read,
# the refill, the charge and the write happen with nothing interleaved: two
# workers charging the same subject at the same instant must spend two tokens,
# not the same one twice, and a read-modify-write from Python could not promise
# that without a second round trip and a lock.
#
# The clock is Redis's own `TIME`, never the caller's. Four uvicorn workers on
# one box agree closely enough that it would usually not matter; a caller on
# another host with a drifting clock could otherwise refill its own bucket by
# claiming time had passed, which is a limiter that can be argued out of its
# limit. `TIME` is non-deterministic and therefore forbidden inside a script
# replicated verbatim — Redis has replicated scripts by their effects since
# 5.0, so this is allowed on every version this deployment can run on.
#
# Floats are written back as strings deliberately: a bucket holding 119.4
# tokens must not round to 119 on every write, or a caller loses a fraction of
# a token per request and the configured rate is quietly not the served rate.
BUCKET_SCRIPT = """
local capacity = tonumber(ARGV[1])
local refill = tonumber(ARGV[2])
local cost = tonumber(ARGV[3])
local ttl = tonumber(ARGV[4])

local clock = redis.call('TIME')
local now = tonumber(clock[1]) + tonumber(clock[2]) / 1000000

local state = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(state[1])
local ts = tonumber(state[2])
if tokens == nil or ts == nil then
  tokens = capacity
  ts = now
end

-- A negative elapsed is a clock that stepped backwards under this bucket
-- (a failover onto a replica whose time is behind). Treat it as no time at
-- all rather than as a debt the caller has to pay off.
local elapsed = now - ts
if elapsed < 0 then
  elapsed = 0
end
tokens = math.min(capacity, tokens + elapsed * refill)

local allowed = 0
if tokens >= cost then
  allowed = 1
  tokens = tokens - cost
end

redis.call('HSET', KEYS[1], 'tokens', tostring(tokens), 'ts', tostring(now))
redis.call('EXPIRE', KEYS[1], ttl)

return {allowed, tostring(tokens)}
"""


@dataclass(frozen=True)
class RedisEndpoint:
    """Where the online store is, as the deployment spells it."""

    host: str
    port: int
    password: str | None
    ssl: bool


def parse_redis_endpoint(connection_string: str) -> RedisEndpoint:
    """Read Feast's ``host:port,key=value,...`` form.

    Feast's Redis online store does not take a ``redis://`` URL, so this is the
    only shape the deployment ever holds — the same string
    ``docker-compose.prod.yml`` derives from ``REDIS_PASSWORD`` for every
    service that touches the online store. ``src/release/bootstrap.py`` parses
    it too, for the preflight's reachability probe; the two are separate
    because the preflight must stay importable with no Redis client installed,
    and ``tests/unit/test_ratelimit.py`` asserts they agree on every form the
    deployment uses.
    """
    parts = [part.strip() for part in connection_string.split(",") if part.strip()]
    if not parts:
        raise ValueError("REDIS_CONNECTION_STRING is empty")
    host, separator, port = parts[0].partition(":")
    if not separator or not host:
        raise ValueError(f"REDIS_CONNECTION_STRING must start with host:port; got {parts[0]!r}")
    try:
        port_number = int(port)
    except ValueError as exc:
        raise ValueError(f"REDIS_CONNECTION_STRING port {port!r} is not a number") from exc

    options: dict[str, str] = {}
    for part in parts[1:]:
        key, separator, value = part.partition("=")
        if not separator:
            raise ValueError(
                f"REDIS_CONNECTION_STRING option {part!r} is not a key=value pair; Feast's "
                "form is host:port,password=...,ssl=true"
            )
        options[key.strip().lower()] = value.strip()

    return RedisEndpoint(
        host=host,
        port=port_number,
        password=options.get("password") or None,
        ssl=options.get("ssl", "").lower() == "true",
    )


class SharedTokenBucket:
    """One token bucket per ``(tenant, subject)``, in Redis, for every worker.

    The decision — admitted, tokens left — comes back from a single ``EVALSHA``
    so the limiter costs one round trip on a path with a p99 SLO
    (non-negotiable #4). On the deployed host that is a sub-millisecond hop
    across the private Docker network to a Redis the online feature store
    already runs; it is a real cost, paid by every request to make the small
    fraction that get refused mean something.

    **Fail open.** Any Redis error — unreachable, timed out, a script that will
    not load — falls back to ``self._fallback``'s in-process bucket for that
    request and logs it, rate-limited. A limiter is backpressure, not an auth
    boundary: failing closed would turn a Redis blip into a total outage for a
    service that can otherwise still serve popularity fallbacks, and would be
    the only place in this API where an infrastructure failure is fatal rather
    than degrading. The cost is that the guarantee weakens exactly when Redis
    is unwell — which is why the fallback is a real bucket rather than an
    unconditional yes, and why ``/readyz`` reports the degradation.
    """

    def __init__(
        self,
        *,
        # `Redis[str]` because the client is built with `decode_responses=True`:
        # the script's token count comes back as a string so a fractional
        # bucket survives the round trip.
        redis_client: aioredis.Redis[str],
        requests_per_minute: int,
        burst: int,
        fallback: TokenBucketLimiter | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._policy = RateLimitPolicy(requests_per_minute=requests_per_minute, burst=burst)
        self._redis = redis_client
        # The fallback carries the same default policy on purpose: a request
        # that fails open must not silently get a different allowance.
        self._fallback = fallback or TokenBucketLimiter(
            requests_per_minute=requests_per_minute, burst=burst, clock=clock
        )
        self._clock = clock
        self._script = redis_client.register_script(BUCKET_SCRIPT)
        self._lock = threading.Lock()
        self._fail_open_total = 0
        self._fail_open_since_log = 0
        self._last_log_at = float("-inf")
        self._last_error_at = float("-inf")

    @property
    def policy(self) -> RateLimitPolicy:
        return self._policy

    @property
    def fail_open_total(self) -> int:
        """How many requests have fallen back since this worker started."""
        return self._fail_open_total

    def describe(self) -> str:
        return self._policy.describe()

    async def charge(
        self, key: RateLimitKey, policy: RateLimitPolicy | None = None
    ) -> RateLimitDecision:
        effective = policy or self._policy
        tenant_id, subject = key
        try:
            allowed, tokens = await self._script(
                keys=[bucket_key(tenant_id, subject)],
                args=[
                    effective.capacity,
                    effective.refill_per_second,
                    1,
                    effective.ttl_seconds,
                ],
            )
            # Inside the try with the call: a reply this cannot read is a
            # limiter that is not working, and the answer to that is the same
            # fail-open as an unreachable Redis. A 500 on the way to deciding
            # whether to allow a request would be the limiter taking down the
            # thing it exists to protect.
            decision = _decision(allowed=bool(int(allowed)), tokens=float(tokens), policy=effective)
        except Exception as exc:  # noqa: BLE001 - see the fail-open note above
            self._record_fail_open(exc)
            return self._fallback.acquire(key, effective)
        return decision

    async def report(self) -> RateLimitBackendState:
        """Reported to ``/readyz``; never gates it, never raises.

        A recent fail-open is reported without asking Redis anything, because
        the traffic already answered the question and a probe that contradicts
        it would be describing a connection the requests are not using. With no
        recent failure the probe pings, so a service that has taken no traffic
        since boot still reports the truth rather than its configuration.
        """
        if self._clock() - self._last_error_at < DEGRADED_WINDOW_SECONDS:
            return "degraded"
        try:
            await self._redis.ping()
        except Exception:  # noqa: BLE001 - a readiness probe reports, it never raises
            logger.info("Readiness probe: the rate-limit bucket's Redis is unreachable")
            return "degraded"
        return "shared"

    def _record_fail_open(self, exc: BaseException) -> None:
        """Count every fallback; log at most one line per window.

        Under a Redis outage the alternative is one warning per request, which
        buries the incident it is reporting. The line carries the count it
        stands for so the volume is still legible.
        """
        with self._lock:
            now = self._clock()
            self._fail_open_total += 1
            self._fail_open_since_log += 1
            self._last_error_at = now
            if now - self._last_log_at < FAIL_OPEN_LOG_INTERVAL_SECONDS:
                return
            suppressed = self._fail_open_since_log
            self._last_log_at = now
            self._fail_open_since_log = 0
        logger.warning(
            "rate_limit_backend=redis outcome=fail_open requests=%d total=%d error=%s: %s "
            "(charged the in-process bucket instead; the configured limit is per worker "
            "until Redis answers again)",
            suppressed,
            self._fail_open_total,
            type(exc).__name__,
            exc,
        )


def build_rate_limiter(settings: Settings) -> RateLimiter:
    """Pick a backend from ``Settings`` and build it.

    ``redis`` is the default and the deployed choice; ``memory`` exists for
    tests and for a rig that deliberately wants a process-local bucket, and
    ``Settings`` refuses it outside dev without an explicit acknowledgement so
    that a deployment cannot quietly run the per-worker approximation while
    advertising a service-wide limit.
    """
    if settings.rate_limit_backend == "memory":
        return TokenBucketLimiter(
            requests_per_minute=settings.rate_limit_requests_per_minute,
            burst=settings.rate_limit_burst,
        )
    endpoint = parse_redis_endpoint(settings.redis_connection_string)
    return SharedTokenBucket(
        redis_client=aioredis.Redis(
            host=endpoint.host,
            port=endpoint.port,
            password=endpoint.password,
            ssl=endpoint.ssl,
            # Bounded and short on purpose. This client sits in front of a p99
            # SLO, so a Redis that has stopped answering must cost the request
            # a timeout it can afford rather than the whole latency budget: the
            # request then falls back to the in-process bucket and carries on.
            socket_timeout=settings.rate_limit_redis_timeout_seconds,
            socket_connect_timeout=settings.rate_limit_redis_timeout_seconds,
            max_connections=settings.rate_limit_redis_max_connections,
            # The single most important line here, and it is not the timeouts.
            # redis-py's default is three retries behind an exponential
            # backoff with a one-second base, so a refused connection — which
            # returns instantly — was measured taking 6.7 s per call before
            # this client would give up and fail open. That is 67 times the
            # whole p99 budget, spent inside a middleware whose entire promise
            # is that it costs one round trip. One attempt, no backoff: the
            # fallback bucket is the retry.
            retry=Retry(NoBackoff(), retries=0),
            decode_responses=True,
        ),
        requests_per_minute=settings.rate_limit_requests_per_minute,
        burst=settings.rate_limit_burst,
    )


class RateLimitMiddleware:
    """Charge the bucket after the token is verified and before the handler.

    Registered inside ``AuthMiddleware`` (which resolves the principal) and
    outside ``RecommendationAuditMiddleware`` (so a throttled request writes no
    prediction-audit row — it made no prediction, and a limiter that amplifies
    a burst into a write per rejected request is working against itself).

    A raw ASGI middleware rather than ``BaseHTTPMiddleware`` for the same reason
    ``RequestIdMiddleware`` is one: wrapping ``send`` costs a closure per
    request instead of an extra task and a queue on the p99-critical path.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        limiter: RateLimiter,
        tenant_limits: TenantLimitResolver | None = None,
        exempt_paths: frozenset[str] = UNAUTHENTICATED_PATHS,
    ) -> None:
        self.app = app
        self._limiter = limiter
        # Where a tenant's own quota is looked up (migration 0016). Absent — as
        # in every test that only cares about the bucket arithmetic — means
        # every tenant charges the settings policy, which is what this
        # middleware did before the columns existed.
        self._tenant_limits = tenant_limits
        # The same frozenset the auth middleware and the OpenAPI generator use.
        # `/healthz` and `/readyz` carry no identity to key a bucket on, and a
        # throttled liveness probe would take a healthy service out of rotation.
        self._exempt_paths = exempt_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") in self._exempt_paths:
            await self.app(scope, receive, send)
            return

        state = scope.get("state", {})
        principal = state.get("principal")
        if principal is None:
            # Nothing verified this caller, so there is no key to charge. In
            # practice unreachable — the auth middleware answers 401 before
            # calling us — but a limiter must fail open on a missing identity
            # rather than invent a shared bucket every anonymous request lands
            # in, which is a denial-of-service amplifier, not a limit.
            await self.app(scope, receive, send)
            return

        # The request transaction the auth middleware opened, handed to the
        # resolver so a registry read reuses this request's connection instead
        # of asking the pool for a second one while this one is still held.
        policy = await self._policy_for(principal.tenant_id, state.get("db"))
        decision = await self._limiter.charge((principal.tenant_id, principal.user_id), policy)
        headers = {
            LIMIT_HEADER: str(decision.limit),
            REMAINING_HEADER: str(decision.remaining),
            RESET_HEADER: str(decision.reset_seconds),
        }

        if not decision.allowed:
            response = JSONResponse(
                {
                    "detail": (
                        f"rate limit exceeded for this tenant and subject "
                        f"({policy.describe()}); retry in "
                        f"{decision.retry_after_seconds}s"
                    )
                },
                status_code=429,
                headers={**headers, RETRY_AFTER_HEADER: str(decision.retry_after_seconds)},
            )
            await response(scope, receive, send)
            return

        async def send_with_rate_limit_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_headers = MutableHeaders(scope=message)
                for name, value in headers.items():
                    response_headers[name] = value
            await send(message)

        await self.app(scope, receive, send_with_rate_limit_headers)

    async def _policy_for(self, tenant_id: str, connection: object | None) -> RateLimitPolicy:
        """What limit applies to this caller. The only place that answers it.

        A tenant with no overrides gets the settings policy the limiter was
        built with, so the common deployment resolves to one shared object and
        allocates nothing per tenant. A tenant with either column set gets
        ``COALESCE(column, global)`` per column — lowering one tenant's
        sustained rate should not require restating a burst that was already
        right, and a half-specified override is a normal configuration rather
        than an error.

        The policy is an argument to ``charge`` rather than a property of a
        limiter object, which is what makes this cheap: a per-tenant quota
        needs no second bucket set, and a quota that changes takes effect on
        the very next request. It also does not reset anyone's bucket — the
        Redis-held token count survives the change and the script clamps it to
        the new capacity, so raising a quota is not a way for a throttled
        caller to buy a fresh allowance and lowering one takes effect at once.
        """
        if self._tenant_limits is None:
            return self._limiter.policy
        overrides = await self._tenant_limits(tenant_id, connection)
        if overrides.is_default:
            return self._limiter.policy
        default = self._limiter.policy
        return RateLimitPolicy(
            requests_per_minute=overrides.requests_per_minute or default.requests_per_minute,
            burst=overrides.burst or default.burst,
        )
