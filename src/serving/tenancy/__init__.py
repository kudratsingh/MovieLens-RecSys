"""
Tenant router per ADR 0008. Resolves a ``tenant_id`` (derived from the
auth-provider realm by the middleware) to the tenant's metadata and
scoping knobs — display name, Redis key prefix, and (in later bundles)
champion model version + per-tenant rate limits.

Bundle #1b ships the read path only. Champion model routing arrives
with the serving bundles (Phase 3 code that consumes ADR 0007 + 0008);
per-tenant rate limits arrive with the audit-log + rate-limit bundle.
"""

from src.serving.tenancy.router import TenantConfig, TenantRouter, UnknownTenantError

__all__ = ["TenantConfig", "TenantRouter", "UnknownTenantError"]
