"""
Tenant router per ADR 0008. Resolves a ``tenant_id`` (derived from the
auth-provider realm by the middleware) to the tenant's metadata and
scoping knobs — display name and Redis key prefix.

Per-tenant champion model version and rate limits are not columns on
this row yet. Today the model sidecar loads a single tenant-pinned
artifact manifest (``src/models/artifacts.py``) and rejects requests
for any other tenant, and rate limiting is still outstanding. When
those land they are additional columns on the same read path.
"""

from src.serving.tenancy.router import TenantConfig, TenantRouter, UnknownTenantError

__all__ = ["TenantConfig", "TenantRouter", "UnknownTenantError"]
