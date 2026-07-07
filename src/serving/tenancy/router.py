"""
Tenant router — reads ``public.tenants`` for tenant metadata.

Runs on the admin_user engine (BYPASSRLS) because ``public.tenants`` is
a cross-tenant registry by definition (see ADR 0008 §decision — the
tenant registry lives at the ``public`` level and is not RLS-scoped).
Using admin_user here keeps the tenant lookup consistent regardless of
which application role happens to be querying, and doesn't require the
lookup to run inside the per-request tenant-scoped transaction the auth
middleware opens.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Engine, text


@dataclass(frozen=True)
class TenantConfig:
    """Metadata for one tenant. What we return from ``resolve()``.

    ``redis_prefix`` is the ``tenant:<id>:`` key namespace ADR 0008 and
    ADR 0009 both use — every online-store read the FastAPI service
    performs is scoped through this prefix by construction. Composing
    it here rather than in every handler prevents the "forgot the
    prefix" class of leak.
    """

    id: str
    display_name: str
    redis_prefix: str


class UnknownTenantError(Exception):
    """Raised when ``resolve()`` is asked about a tenant that isn't in
    ``public.tenants``. This is a 403 to the caller — a valid token
    for a realm we don't recognize as a tenant is a misconfiguration
    on the auth side (someone provisioned a Keycloak realm without
    the matching DB row) that must not silently pass through."""


class TenantRouter:
    """Reads tenant configuration from ``public.tenants``.

    Bundle #1b ships the resolve path only. Later bundles extend
    ``TenantConfig`` with the champion model version and per-tenant
    rate-limit knobs — same read path, additional columns.
    """

    def __init__(self, admin_engine: Engine) -> None:
        self._engine = admin_engine

    def resolve(self, tenant_id: str) -> TenantConfig:
        """Return the ``TenantConfig`` for ``tenant_id``. Raises
        ``UnknownTenantError`` if no such tenant exists.
        """
        with self._engine.connect() as conn:
            row = conn.execute(
                text("SELECT id, display_name FROM public.tenants WHERE id = :tid"),
                {"tid": tenant_id},
            ).one_or_none()
        if row is None:
            raise UnknownTenantError(f"unknown tenant: {tenant_id!r}")
        return TenantConfig(
            id=row.id,
            display_name=row.display_name,
            redis_prefix=f"tenant:{row.id}:",
        )
