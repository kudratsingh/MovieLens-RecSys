"""Per-``(tenant, subject)`` request rate limiting — ADR 0014.

The key is taken from the *verified* token: the tenant the issuer resolved to
and the ``sub`` claim the middleware put on ``request.state.principal``. It is
never a client address. Behind Railway's edge, Compose's bridge network, or the
Caddy rehearsal edge every request arrives from a proxy, so an address-keyed
limiter would either throttle the whole deployment as one caller or force
``--forwarded-allow-ips`` into a security decision. A verified token cannot be
spoofed by a header, which is what makes it the only honest key here.

The bucket is a token bucket rather than a fixed window: a fixed window lets a
caller spend a whole minute's allowance in the last second of one window and
again in the first second of the next, which is the burst the ranking path
cannot absorb. Capacity is ``RATE_LIMIT_BURST``, refill is
``RATE_LIMIT_REQUESTS_PER_MINUTE / 60`` tokens per second.

Two limits of this implementation are deliberate and are written down in
ADR 0014 rather than hidden:

  * **The bucket lives in the worker process.** A service running N uvicorn
    workers therefore admits up to N times the configured rate, because the
    OS spreads a caller's connections across the workers' accept queues. The
    upgrade path is a Redis-backed shared bucket — the Redis instance already
    exists for the online feature store — and it is deferred because a shared
    bucket puts a network round trip on the p99 path this project has an SLO on.
  * **The limits are global, not per tenant.** ``public.tenants`` is where a
    per-tenant quota column belongs, alongside the champion-model-version and
    A/B-seed columns Phase 6 adds; adding a quota column here would mean
    reading it on every request before the tenant-config work that makes such
    a read cached and cheap.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src.auth.middleware import UNAUTHENTICATED_PATHS

LIMIT_HEADER = "X-RateLimit-Limit"
REMAINING_HEADER = "X-RateLimit-Remaining"
RESET_HEADER = "X-RateLimit-Reset"
RETRY_AFTER_HEADER = "Retry-After"

# Ceiling on distinct live buckets. A bucket is 24 bytes of floats plus its key,
# so this is kilobytes rather than megabytes; the ceiling exists so that a
# deployment facing many subjects cannot turn the limiter into an unbounded map.
DEFAULT_MAX_BUCKETS = 4_096

RateLimitKey = tuple[str, str]


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


class TokenBucketLimiter:
    """In-process token buckets keyed by ``(tenant_id, subject)``.

    Every operation is O(1) and touches no I/O — the limiter sits in front of
    the recommendation path, which has a p99 SLO (non-negotiable #4), so it can
    afford a dict lookup and some arithmetic and nothing else. The eviction
    sweep is the one exception and only runs when the map is over its ceiling.
    """

    def __init__(
        self,
        *,
        requests_per_minute: int,
        burst: int,
        max_buckets: int = DEFAULT_MAX_BUCKETS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if requests_per_minute <= 0:
            raise ValueError("requests_per_minute must be positive")
        if burst <= 0:
            raise ValueError("burst must be positive")
        if max_buckets <= 0:
            raise ValueError("max_buckets must be positive")
        self.requests_per_minute = requests_per_minute
        self.burst = burst
        self._refill_per_second = requests_per_minute / 60.0
        self._capacity = float(burst)
        self._max_buckets = max_buckets
        # Monotonic by default: a clock that steps backwards over an NTP
        # correction would hand out an unbounded allowance, and one that steps
        # forwards would throttle a caller who did nothing wrong.
        self._clock = clock
        self._lock = threading.Lock()
        self._buckets: dict[RateLimitKey, tuple[float, float]] = {}

    @property
    def bucket_count(self) -> int:
        with self._lock:
            return len(self._buckets)

    def describe(self) -> str:
        """One-line policy summary, used in the 429 body and the boot log."""
        return f"{self.requests_per_minute} requests/minute with a burst of {self.burst}"

    def acquire(self, key: RateLimitKey) -> RateLimitDecision:
        """Charge one request against ``key``'s bucket and report the result."""
        now = self._clock()
        with self._lock:
            tokens, updated_at = self._buckets.get(key, (self._capacity, now))
            elapsed = max(0.0, now - updated_at)
            tokens = min(self._capacity, tokens + elapsed * self._refill_per_second)
            allowed = tokens >= 1.0
            if allowed:
                tokens -= 1.0
            self._buckets[key] = (tokens, now)
            if len(self._buckets) > self._max_buckets:
                self._evict(now)
        return RateLimitDecision(
            allowed=allowed,
            limit=self.burst,
            remaining=int(tokens),
            reset_seconds=math.ceil((self._capacity - tokens) / self._refill_per_second),
            # Never advertise 0: a client that reads Retry-After as "sleep this
            # long" would spin. The floor is one whole second.
            retry_after_seconds=(
                0 if allowed else max(1, math.ceil((1.0 - tokens) / self._refill_per_second))
            ),
        )

    def _evict(self, now: float) -> None:
        """Drop the fullest buckets until the map is back under its ceiling.

        A bucket that has refilled to capacity is indistinguishable from one
        that was never created, so dropping it changes no future decision at
        all — those go first and are usually the whole sweep. When every live
        bucket is mid-recovery the next-fullest goes, because the caller with
        the most headroom is the one who loses the least by being forgotten.

        The obvious alternative, least-recently-used, is wrong here: the least
        recently *seen* bucket is typically the caller who was just refused and
        backed off, so LRU would reach the throttled caller first and hand them
        a fresh allowance — turning the ceiling into a way around the limit.
        """
        ranked = sorted(
            (
                (-self._refilled(tokens, updated_at, now), key)
                for key, (tokens, updated_at) in self._buckets.items()
            ),
        )
        for _, key in ranked:
            if len(self._buckets) <= self._max_buckets:
                break
            del self._buckets[key]

    def _refilled(self, tokens: float, updated_at: float, now: float) -> float:
        return min(self._capacity, tokens + max(0.0, now - updated_at) * self._refill_per_second)


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
        limiter: TokenBucketLimiter,
        exempt_paths: frozenset[str] = UNAUTHENTICATED_PATHS,
    ) -> None:
        self.app = app
        self._limiter = limiter
        # The same frozenset the auth middleware and the OpenAPI generator use.
        # `/healthz` and `/readyz` carry no identity to key a bucket on, and a
        # throttled liveness probe would take a healthy service out of rotation.
        self._exempt_paths = exempt_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") in self._exempt_paths:
            await self.app(scope, receive, send)
            return

        principal = scope.get("state", {}).get("principal")
        if principal is None:
            # Nothing verified this caller, so there is no key to charge. In
            # practice unreachable — the auth middleware answers 401 before
            # calling us — but a limiter must fail open on a missing identity
            # rather than invent a shared bucket every anonymous request lands
            # in, which is a denial-of-service amplifier, not a limit.
            await self.app(scope, receive, send)
            return

        decision = self._limiter.acquire((principal.tenant_id, principal.user_id))
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
                        f"({self._limiter.describe()}); retry in "
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
