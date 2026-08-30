"""The machinery under the canaries: row-level security, asked directly.

Every isolation assertion in ``test_no_cross_tenant_leak.py`` is of the form
"tenant A's response did not contain tenant B's data". That sentence is true of
a correctly isolated system and equally true of a system where tenant B has no
data, or where the read never reached a database at all. The endpoint canaries
close the first hole with a sentinel each tenant can see in its own payloads
(issue #75); this module closes the second, one layer down, with the same row
read three times over connections that differ in exactly one thing:

  1. ``admin_user`` — BYPASSRLS — sees the row. It exists.
  2. ``app_user`` with ``app.tenant_id`` set to the owning tenant — sees it.
     The application's own role, its own connection, its own GUC.
  3. ``app_user`` with ``app.tenant_id`` set to the *other* tenant — does not.
     Nothing changed but the setting the auth middleware derives from the
     token issuer.

Read together those three are the negative-of-the-negative: (1) rules out an
empty table, (2) rules out a broken query or a wrong database, and only then
does (3) mean the policy is what hid the row. A fourth case — no
``app.tenant_id`` at all — is here because ADR 0008 pins fail-closed as the
behaviour of a request that reaches Postgres without a resolved tenant, and
``current_setting(..., true)`` returning NULL is what delivers it.
"""

from __future__ import annotations

import pytest
from sqlalchemy import Engine, text

from tests.tenant_isolation.conftest import (
    DEFAULT_TENANT,
    DEMO_TENANT,
    TENANT_PAIRS,
    TenantCanary,
)

_PERSONA_NAMES = text("SELECT display_name FROM demo_personas WHERE user_id = :user_id")
_SHARED_MOVIE = text('SELECT count(*) FROM movies WHERE "movieId" = :movie_id')
_BYPASSES_RLS = text("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")


def _read_persona_names(engine: Engine, *, tenant_id: str | None, user_id: int) -> list[str]:
    """The persona display names one connection can see for a user id.

    ``set_config(..., true)`` is ``SET LOCAL`` in a form that takes a bind
    parameter, so the tenant travels as a value rather than as interpolated SQL
    — and, being local, it dies with the transaction the way the middleware's
    does under pgBouncer's transaction pooling.
    """
    with engine.begin() as connection:
        if tenant_id is not None:
            connection.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": tenant_id},
            )
        rows = connection.execute(_PERSONA_NAMES, {"user_id": user_id}).scalars()
        return [str(name) for name in rows]


def test_the_two_connections_differ_only_in_whether_rls_applies(
    rls_engine: Engine, bypass_rls_engine: Engine
) -> None:
    """The premise the rest of this module rests on, asserted rather than assumed.

    If ``app_user`` turned out to hold BYPASSRLS, or the two DSNs turned out to
    address different databases, every "and the other tenant sees nothing" below
    would still pass and would mean nothing at all.
    """
    with rls_engine.connect() as connection:
        app_bypasses = connection.execute(_BYPASSES_RLS).scalar_one()
        app_database = connection.execute(text("SELECT current_database()")).scalar_one()
        app_role = connection.execute(text("SELECT current_user")).scalar_one()
    with bypass_rls_engine.connect() as connection:
        admin_bypasses = connection.execute(_BYPASSES_RLS).scalar_one()
        admin_database = connection.execute(text("SELECT current_database()")).scalar_one()

    assert app_role == "app_user"
    assert app_bypasses is False, "the API's role must not be able to see past a tenant policy"
    assert admin_bypasses is True, "the control connection must be able to, or it proves nothing"
    assert app_database == admin_database


@pytest.mark.parametrize("tenant", (DEFAULT_TENANT, DEMO_TENANT), ids=lambda t: t.tenant_id)
def test_a_row_only_rls_hides_is_reachable_by_the_control_connection(
    tenant: TenantCanary, rls_engine: Engine, bypass_rls_engine: Engine
) -> None:
    """The sentinel row exists, and the application's role can read it — as its owner."""
    assert _read_persona_names(
        bypass_rls_engine, tenant_id=tenant.tenant_id, user_id=tenant.persona_user_id
    ) == [tenant.persona_name]
    assert _read_persona_names(
        rls_engine, tenant_id=tenant.tenant_id, user_id=tenant.persona_user_id
    ) == [tenant.persona_name]


@pytest.mark.parametrize("owner, other", TENANT_PAIRS, ids=lambda t: t.tenant_id)
def test_the_same_row_is_invisible_to_the_other_tenant_and_to_no_tenant_at_all(
    owner: TenantCanary, other: TenantCanary, rls_engine: Engine
) -> None:
    """Same role, same query, same row — only the resolved tenant moved.

    The unset case is the one a bug actually produces: a code path that reaches
    the database outside the middleware's transaction has no ``app.tenant_id``,
    and ADR 0008 requires that to return nothing rather than everything.
    """
    assert (
        _read_persona_names(rls_engine, tenant_id=other.tenant_id, user_id=owner.persona_user_id)
        == []
    )
    assert _read_persona_names(rls_engine, tenant_id=None, user_id=owner.persona_user_id) == []


def test_the_rls_connection_can_still_read_what_is_shared(rls_engine: Engine) -> None:
    """Movie facts carry no tenant, and the policies must not have caught them.

    Without this, "the other tenant's connection returned no rows" would be
    consistent with a connection that can read nothing — which is isolation by
    breakage, and would hide a real regression the day someone put a policy on
    ``movies``.
    """
    for owner, other in TENANT_PAIRS:
        with rls_engine.begin() as connection:
            connection.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": owner.tenant_id},
            )
            own = connection.execute(
                _SHARED_MOVIE, {"movie_id": owner.recommendation_movie_id}
            ).scalar_one()
            # The title the *other* tenant's rows point at. The film is a shared
            # fact and stays readable; what never crosses is the state hung off
            # it, which is the whole matrix in the sibling module.
            shared = connection.execute(
                _SHARED_MOVIE, {"movie_id": other.recommendation_movie_id}
            ).scalar_one()
        assert own == 1
        assert shared == 1
