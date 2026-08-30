"""
Tenant router — reads ``public.tenants`` for tenant metadata.

``public.tenants`` is a cross-tenant registry by definition (ADR 0008
§decision — the tenant registry lives at the ``public`` level and is not
RLS-scoped), and ``app_user`` holds SELECT on it (migration 0002). So the
lookup runs on whichever engine the caller already has; the serving app hands
it the RLS-applied app engine rather than keeping a BYPASSRLS credential alive
in the request-serving process to read two columns.

The engine is held rather than required because the lookup does not depend on
the per-request tenant-scoped transaction the auth middleware opens — it reads a
table RLS does not apply to. A caller that already has that transaction open
should still hand it over (``resolve(..., connection=...)``): the engine path is
for callers that have no request, and taking a second pooled connection while
holding the first is a deadlock waiting for a busy enough moment.

Since migration 0016 the row also carries the tenant's champion serving
coordinates, its rate-limit overrides and its A/B bucketing seed, which turns
this from a lookup two handlers perform into one every authenticated request
performs. That is what the short-lived cache below is for: ADR 0014 deferred
per-tenant quotas precisely until "the tenant-config work that makes such a
read cached and cheap" existed, and this is that work.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock
from typing import Any

from sqlalchemy import Connection, Engine, text

logger = logging.getLogger(__name__)

# How long a resolved row may be reused. Short enough that a promotion or a
# quota change takes effect on its own within a minute of being committed, long
# enough that a request rate of hundreds per second costs one small query per
# tenant per window rather than one per request. Nothing here is a security
# boundary — RLS and the auth middleware are — so the staleness window buys
# latency without buying risk.
DEFAULT_CACHE_TTL_SECONDS = 30.0

_TENANT_QUERY = text("""
    SELECT id,
           display_name,
           champion_candidate_version,
           champion_ranker_version,
           champion_feature_version,
           rate_limit_requests_per_minute,
           rate_limit_burst,
           ab_bucketing_seed
    FROM public.tenants
    WHERE id = :tid
    """)


@dataclass(frozen=True)
class TenantChampion:
    """The serving manifest a tenant's requests may be answered by.

    The three coordinates of a ``ServingManifest`` (``src/models/artifacts.py``)
    kept as three fields rather than one string, because they version
    independently: a bundle can ship a new ranker over the same candidate index,
    and a comparison that had to parse a concatenation to notice would be a
    comparison nobody trusts.

    A tenant with no champion has no ``TenantChampion`` at all — see
    ``TenantConfig.champion`` — rather than one full of empty strings.
    """

    candidate_version: str
    ranker_version: str
    feature_version: str

    def matches(
        self,
        *,
        candidate_version: str,
        ranker_version: str,
        feature_version: str,
    ) -> bool:
        return (
            self.candidate_version == candidate_version
            and self.ranker_version == ranker_version
            and self.feature_version == feature_version
        )

    def describe(self) -> str:
        return f"{self.candidate_version}/{self.ranker_version}/{self.feature_version}"


@dataclass(frozen=True)
class TenantRateLimit:
    """A tenant's rate-limit overrides. ``None`` means "use the global setting".

    Each field falls back on its own (ADR 0014's 2026-08-29 note): lowering one
    tenant's sustained rate should not require restating a burst that was
    already right, and a half-specified override is a normal configuration, not
    an error.
    """

    requests_per_minute: int | None = None
    burst: int | None = None

    @property
    def is_default(self) -> bool:
        return self.requests_per_minute is None and self.burst is None


NO_TENANT_OVERRIDES = TenantRateLimit()


@dataclass(frozen=True)
class TenantConfig:
    """Metadata for one tenant. What we return from ``resolve()``.

    ``redis_prefix`` is the ``tenant:<id>:`` key namespace ADR 0008 and
    ADR 0009 both use — every online-store read the FastAPI service
    performs is scoped through this prefix by construction. Composing
    it here rather than in every handler prevents the "forgot the
    prefix" class of leak.

    ``ab_bucketing_seed`` is not read by anything yet; Phase 6's routing layer
    is what will hash it together with a user id to pick an arm. It is resolved
    here anyway because the alternative — a second read path bolted on when that
    lands — is how a config row acquires two sources of truth.
    """

    id: str
    display_name: str
    redis_prefix: str
    ab_bucketing_seed: int
    champion: TenantChampion | None = None
    rate_limit: TenantRateLimit = NO_TENANT_OVERRIDES


class UnknownTenantError(Exception):
    """Raised when ``resolve()`` is asked about a tenant that isn't in
    ``public.tenants``. This is a 403 to the caller — a valid token
    for a realm we don't recognize as a tenant is a misconfiguration
    on the auth side (someone provisioned a Keycloak realm without
    the matching DB row) that must not silently pass through."""


class TenantRouter:
    """Reads tenant configuration from ``public.tenants``.

    Resolves the id, display name and Redis prefix, plus the champion serving
    coordinates, rate-limit overrides and A/B bucketing seed migration 0016
    added. Results are cached for ``cache_ttl_seconds`` because the recommend
    path and the rate limiter now both ask on every request.

    A miss is not cached. An unknown tenant means a verified realm with no
    registry row, which the auth middleware already refuses at the boundary, so
    the case is a misconfiguration to be fixed rather than a hot path to
    optimize — and caching it would mean a freshly registered tenant stayed
    broken for a TTL after someone fixed it.
    """

    def __init__(
        self,
        engine: Engine,
        *,
        cache_ttl_seconds: float = DEFAULT_CACHE_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._engine = engine
        self._cache_ttl_seconds = cache_ttl_seconds
        # Monotonic for the same reason the rate limiter's is: an NTP step
        # backwards must not pin a stale row in place until the wall clock
        # catches up.
        self._clock = clock
        self._lock = Lock()
        self._cache: dict[str, tuple[TenantConfig, float]] = {}

    def resolve(self, tenant_id: str, *, connection: Connection | None = None) -> TenantConfig:
        """Return the ``TenantConfig`` for ``tenant_id``, reading through the
        cache. Raises ``UnknownTenantError`` if no such tenant exists.

        Performs I/O on a miss, so an async caller must not call it on the
        event loop — use ``cached()`` first and hand the miss to a thread.

        ``connection`` is the request's own transaction, and a caller that has
        one should pass it. Taking a second connection from the pool while
        holding the first is how a pool deadlocks: every in-flight request here
        already holds one for the length of its RLS transaction, so a cache
        expiry that all of them miss at once would have each of them waiting on
        a connection none of them can release. Reading the registry on the
        connection already in hand cannot deadlock and costs no pool slot.
        ``public.tenants`` carries no RLS policy, so the row is the same one
        either way (ADR 0008).
        """
        cached = self.cached(tenant_id)
        if cached is not None:
            return cached
        if connection is not None:
            row = connection.execute(_TENANT_QUERY, {"tid": tenant_id}).one_or_none()
        else:
            with self._engine.connect() as conn:
                row = conn.execute(_TENANT_QUERY, {"tid": tenant_id}).one_or_none()
        if row is None:
            raise UnknownTenantError(f"unknown tenant: {tenant_id!r}")
        config = TenantConfig(
            id=row.id,
            display_name=row.display_name,
            redis_prefix=f"tenant:{row.id}:",
            ab_bucketing_seed=int(row.ab_bucketing_seed),
            champion=_champion_from_row(row),
            rate_limit=TenantRateLimit(
                requests_per_minute=row.rate_limit_requests_per_minute,
                burst=row.rate_limit_burst,
            ),
        )
        with self._lock:
            self._cache[config.id] = (config, self._clock() + self._cache_ttl_seconds)
        return config

    def cached(self, tenant_id: str) -> TenantConfig | None:
        """Return a live cached config, or ``None``. Never performs I/O.

        This is the half an async caller may run on the event loop: the fast
        path of every authenticated request is a dict read under a lock held
        for the length of one comparison.
        """
        with self._lock:
            entry = self._cache.get(tenant_id)
            if entry is None:
                return None
            config, expires_at = entry
            if self._clock() >= expires_at:
                del self._cache[tenant_id]
                return None
            return config

    def invalidate(self, tenant_id: str | None = None) -> None:
        """Drop one tenant's cached row, or all of them.

        Nothing calls this in Phase 3 — a champion moves by migration, and the
        TTL is the propagation mechanism. It exists because Phase 6's promotion
        job is the thing that will want a promotion to take effect now rather
        than within a window, and the alternative it would otherwise reach for
        is a process restart.
        """
        with self._lock:
            if tenant_id is None:
                self._cache.clear()
            else:
                self._cache.pop(tenant_id, None)


def _champion_from_row(row: Any) -> TenantChampion | None:
    """Build the champion, or ``None`` when this tenant has no learned serving.

    ``ck_tenants_champion_complete`` (migration 0016) makes a partially named
    champion impossible in the database, so the middle branch here is
    unreachable through any supported write path. It is still handled, because
    the failure it guards against — ranking under a version nobody registered —
    is worse than the popularity fallback it degrades to, and because a schema
    restored from somewhere without that constraint should not be able to talk
    this process into serving anyway.
    """
    candidate = row.champion_candidate_version
    ranker = row.champion_ranker_version
    feature = row.champion_feature_version
    present = [value for value in (candidate, ranker, feature) if value]
    if not present:
        return None
    if len(present) != 3:
        logger.error(
            "tenant_champion_incomplete tenant_id=%s candidate=%r ranker=%r feature=%r",
            row.id,
            candidate,
            ranker,
            feature,
        )
        return None
    return TenantChampion(
        candidate_version=str(candidate),
        ranker_version=str(ranker),
        feature_version=str(feature),
    )
