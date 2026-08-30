"""
Tenant router per ADR 0008. Resolves a ``tenant_id`` (derived from the
auth-provider realm by the middleware) to the tenant's metadata and
scoping knobs — display name, Redis key prefix, champion serving
coordinates, rate-limit overrides and A/B bucketing seed.

Migration 0016 put the last three on the row. What that changes is what the
registry can *express*, not yet how many models are deployed: the sidecar still
loads one bundle for one tenant (``MODEL_TENANT_ID``) and refuses anything else,
and a second tenant on a second model needs Phase 6's routing layer. What is no
longer true is that the answer lives only in an environment variable — a
tenant's champion is a row now, the coordinator sends it with every rank
request, and a bundle that does not match it fails closed to popularity with an
audited reason.
"""

from src.serving.tenancy.router import (
    NO_TENANT_OVERRIDES,
    TenantChampion,
    TenantConfig,
    TenantRateLimit,
    TenantRouter,
    UnknownTenantError,
)

__all__ = [
    "NO_TENANT_OVERRIDES",
    "TenantChampion",
    "TenantConfig",
    "TenantRateLimit",
    "TenantRouter",
    "UnknownTenantError",
]
