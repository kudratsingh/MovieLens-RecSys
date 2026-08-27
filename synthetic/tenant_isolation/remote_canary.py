"""Cross-tenant leakage canary that can be pointed at a deployed stack.

Non-negotiable #9 is the highest-severity bug class in this system, and until
now the only thing enforcing it was ``test_no_cross_tenant_leak.py``, which
mounts the FastAPI app in-process against a local Compose stack. That test is
the right gate for CI and the wrong instrument for a deployment: it needs the
application object, the database URL and the seeded canary rows, none of which a
verification job outside the cluster has.

This module is the deployment-side half. It speaks HTTP only, takes every
identity as a flag, and asks the two questions that survive being asked from
outside:

  A. An authenticated actor from tenant A, deliberately *without* the
     ``demo-impersonator`` role, is refused on every persona-scoped route when
     it names tenant B's persona ids. Exactly 403 is required. A 404 would also
     be a denial, but by persona scoping rather than by the guard -- a right
     answer for the wrong reason, and evidence that the isolation actor is
     holding a role it was created not to have. So it is reported as a failure
     with that explanation rather than quietly accepted.

  B. An actor from tenant B -- who legitimately holds the persona role inside
     its own tenant -- never sees tenant A's rows. Some of these routes answer
     200 for an unknown user id by design (recommendations falls back to
     popularity, history comes back empty), so a blanket "must not be 2xx" would
     be wrong. The property that actually holds is stronger and simpler: no
     response body may carry a ``tenant_id`` other than the caller's, anywhere
     in its payload.

**An unreachable target is a hard failure, never a skip.** A verification
harness that reports success because it could not reach the thing it verifies is
worse than no harness at all: it converts an outage into a green check. Every
exit path here is either "the assertions ran and held" or a non-zero status.

**Safe to run against production.** The mutation routes are probed because they
are part of the guarded surface, but every one of them is addressed so that a
*broken* guard still fails closed: the per-movie routes name a movie id that
exists in no catalog, and the bulk rating reset -- the one route whose blast
radius is a whole user -- names a user id that is nobody's persona. In both
cases an intact guard answers 403 first, and a broken one answers 404 without
touching a row, which this module still reports as a failure.

It lives beside the other deployment harnesses rather than under ``tests/``
because the serving image copies ``synthetic/`` and not ``tests/``: from
``tests/`` the verify job could only report that it was unable to run the one
check standing behind the project's highest-severity bug class.
``tests/tenant_isolation/`` still owns the in-process CI test and imports this
module for the one switch they share.

Run it as::

    python -m synthetic.tenant_isolation.remote_canary \\
        --api-url http://api.internal:8000 --keycloak-url http://keycloak.internal:8080 \\
        --client-id movielens-verify --client-secret ... \\
        --realm-a default --username isolation --password ... \\
        --realm-b demo --realm-b-username verify --realm-b-password ...

Defaults are the local Compose stack's dev identities. Direction B rehearses
locally as-is; direction A does not, and says so rather than pretending: the dev
realms enable the password grant on exactly one client, ``movielens-api``, which
is also the trusted service client, so a local tenant-A token would be allowed
by ``azp`` and every expected 403 would come back 200. The production realm
templates issue harness tokens from ``movielens-verify`` instead, which is
deliberately never ``KEYCLOAK_SERVICE_CLIENT_ID``; ``assert_denied_by_design``
refuses to run rather than report that difference as a leak.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import sys
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx

# Set by CI (and by any job that must not silently pass), read by this module's
# `live_stack_required` and by the sibling conftest. One name, one meaning: a
# real stack is mandatory here, so its absence is a failure rather than a skip.
REQUIRE_STACK_ENV = "REQUIRE_TENANT_ISOLATION_STACK"
_TRUTHY = frozenset({"1", "true", "yes", "on"})

# Seeded persona ids. Tenant B (`demo`) owns 900000101-900000104; the harness
# only ever names the Action Fan, because the guard denies before it looks at
# which persona was named.
TENANT_B_PERSONA_ID = 900000101
# A user id that belongs to tenant A rather than tenant B, used for direction B.
TENANT_A_USER_ID = 987654321
# No movie carries this id in any tenant's catalog, and no persona carries this
# user id. Together they are what keeps the mutation probes harmless even in the
# world where the guard they are probing has been broken.
UNROUTABLE_MOVIE_ID = 999000999
UNROUTABLE_USER_ID = 999000999

# The realm role that entitles an actor to select personas, and the production
# default for the client id the API trusts by `azp` alone
# (`KEYCLOAK_SERVICE_CLIENT_ID`). The tenant-A actor must hold neither.
PERSONA_ROLE = "demo-impersonator"
DEFAULT_SERVICE_CLIENT_ID = "movielens-api"


class CanaryError(RuntimeError):
    """The run could not establish the thing it exists to establish."""


class UnreachableTargetError(CanaryError):
    """The target could not be reached or could not be authenticated against."""


class ActorMisconfiguredError(CanaryError):
    """The actor that is supposed to be denied would in fact be allowed.

    Direction A only means something if its identity is one the API refuses.
    Two ways a deployment gets that wrong, both of which turn every 403 this
    canary expects into a 200 and read as a catastrophic leak when they are
    really a bad harness credential: the actor holds ``demo-impersonator``, or
    its token was minted by the client named in ``KEYCLOAK_SERVICE_CLIENT_ID``,
    which `RequestPrincipal.can_access_demo_personas` trusts by ``azp`` alone.
    """


def live_stack_required() -> bool:
    """Whether a missing stack must fail rather than skip."""
    return os.environ.get(REQUIRE_STACK_ENV, "").strip().lower() in _TRUTHY


@dataclass(frozen=True)
class Actor:
    """One Keycloak identity, resolved to one tenant by its realm."""

    realm: str
    client_id: str
    client_secret: str
    username: str
    password: str

    @property
    def tenant_id(self) -> str:
        # Realm-per-tenant (ADR 0007): the tenant is derived from the token
        # issuer, never declared by the client, so the realm slug is the tenant.
        return self.realm


@dataclass(frozen=True)
class PersonaRoute:
    """One route behind ``_require_demo_persona_access`` in ``src/serving/app.py``.

    ``template`` is the FastAPI path exactly as the application declares it, so
    ``tests/unit/test_verification_harnesses.py`` can compare this table against
    the running app's route set. A guarded route missing from here is a route no
    deployment ever proves is isolated, and that omission is caught in CI rather
    than discovered in production.
    """

    method: str
    template: str
    query: str = ""
    json_body: Mapping[str, Any] | None = None
    # True for the one route that mutates every rating under a user id rather
    # than a single named movie. It is probed at a user id nobody owns, so a
    # broken guard hits the unknown-persona 404 instead of clearing a real
    # persona's ratings. The 403 assertion is unchanged either way.
    unscoped_mutation: bool = False

    def path(self, *, user_id: int, movie_id: int) -> str:
        return self.template.format(user_id=user_id, movie_id=movie_id) + self.query

    def body(self) -> dict[str, Any] | None:
        return dict(self.json_body) if self.json_body is not None else None


PERSONA_ROUTES: tuple[PersonaRoute, ...] = (
    PersonaRoute("GET", "/personas"),
    PersonaRoute("GET", "/users/{user_id}/recommendations", query="?limit=1"),
    PersonaRoute("GET", "/users/{user_id}/history", query="?limit=1"),
    PersonaRoute("GET", "/users/{user_id}/audits", query="?limit=1"),
    PersonaRoute("GET", "/users/{user_id}/features"),
    PersonaRoute("GET", "/users/{user_id}/catalog", query="?limit=1"),
    PersonaRoute("GET", "/users/{user_id}/library", query="?tab=rated&limit=1"),
    PersonaRoute("GET", "/users/{user_id}/taste-profile"),
    PersonaRoute("GET", "/users/{user_id}/movies/{movie_id}"),
    PersonaRoute("GET", "/users/{user_id}/movies/{movie_id}/state"),
    PersonaRoute("PUT", "/users/{user_id}/ratings/{movie_id}", json_body={"rating": 5.0}),
    PersonaRoute("DELETE", "/users/{user_id}/ratings", unscoped_mutation=True),
    PersonaRoute("PUT", "/users/{user_id}/movies/{movie_id}/watched", json_body={}),
    PersonaRoute("DELETE", "/users/{user_id}/movies/{movie_id}/watched"),
    PersonaRoute("PUT", "/users/{user_id}/movies/{movie_id}/rating", json_body={"rating": 5.0}),
    PersonaRoute("DELETE", "/users/{user_id}/movies/{movie_id}/rating"),
    PersonaRoute("PUT", "/users/{user_id}/movies/{movie_id}/watchlist", json_body={}),
    PersonaRoute("DELETE", "/users/{user_id}/movies/{movie_id}/watchlist"),
    PersonaRoute("PUT", "/users/{user_id}/movies/{movie_id}/dismissal", json_body={}),
    PersonaRoute("DELETE", "/users/{user_id}/movies/{movie_id}/dismissal"),
)


@dataclass
class Finding:
    route: PersonaRoute
    path: str
    passed: bool
    status: int
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "route": f"{self.route.method} {self.path}",
            "passed": self.passed,
            "status": self.status,
            "detail": self.detail,
            "evidence": self.evidence,
        }


def check_guard_denies_foreign_actor(
    client: httpx.Client,
    *,
    api_url: str,
    token: str,
    persona_user_id: int = TENANT_B_PERSONA_ID,
) -> list[Finding]:
    """Tenant A's actor is refused on tenant B's personas, by the guard."""
    findings: list[Finding] = []
    for route in PERSONA_ROUTES:
        user_id = UNROUTABLE_USER_ID if route.unscoped_mutation else persona_user_id
        path = route.path(user_id=user_id, movie_id=UNROUTABLE_MOVIE_ID)
        response = _request(client, api_url, route, path, token)
        if response.status_code == 403:
            findings.append(Finding(route, path, True, 403, "refused by the persona guard"))
            continue
        if response.status_code == 404:
            detail = (
                "denied with 404, so nothing leaked -- but the denial came from persona "
                "scoping, not the persona guard. The isolation actor appears to hold "
                "demo-impersonator; it is created deliberately without it"
            )
        elif 200 <= response.status_code < 300:
            detail = "answered a foreign tenant's persona route with a success status"
        else:
            detail = "expected 403 from the persona guard"
        findings.append(
            Finding(
                route,
                path,
                False,
                response.status_code,
                detail,
                {"body": _body_excerpt(response)},
            )
        )
    return findings


def check_no_foreign_tenant_in_payload(
    client: httpx.Client,
    *,
    api_url: str,
    token: str,
    tenant_id: str,
    user_id: int = TENANT_A_USER_ID,
) -> list[Finding]:
    """Tenant B's actor never receives a row stamped with another tenant."""
    findings: list[Finding] = []
    for route in PERSONA_ROUTES:
        path = route.path(user_id=user_id, movie_id=UNROUTABLE_MOVIE_ID)
        response = _request(client, api_url, route, path, token)
        status = response.status_code
        if 400 <= status < 500:
            findings.append(Finding(route, path, True, status, "denied"))
            continue
        if not 200 <= status < 300:
            # A redirect or a server error is not evidence of isolation, and a
            # canary that shrugs at 503 is the failure mode this module exists
            # to remove.
            findings.append(
                Finding(
                    route,
                    path,
                    False,
                    status,
                    "the target neither answered nor denied",
                    {"body": _body_excerpt(response)},
                )
            )
            continue
        foreign = _foreign_tenants(_parse_json(response), tenant_id)
        if foreign:
            findings.append(
                Finding(
                    route,
                    path,
                    False,
                    status,
                    f"payload carries another tenant's rows: {sorted(foreign)}",
                    {"body": _body_excerpt(response)},
                )
            )
            continue
        findings.append(
            Finding(route, path, True, status, f"answered only for tenant {tenant_id!r}")
        )
    return findings


def _request(
    client: httpx.Client,
    api_url: str,
    route: PersonaRoute,
    path: str,
    token: str,
) -> httpx.Response:
    try:
        return client.request(
            route.method,
            f"{api_url}{path}",
            headers={"Authorization": f"Bearer {token}"},
            json=route.body(),
        )
    except httpx.HTTPError as exc:
        raise UnreachableTargetError(
            f"{route.method} {path} could not be reached at {api_url}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc


def _foreign_tenants(payload: Any, tenant_id: str) -> set[str]:
    """Every `tenant_id` value in the payload that is not the caller's.

    Walks the whole document rather than checking the top-level field: the
    leaks worth catching are a stray item inside a list, not a mislabelled
    envelope.
    """
    return {value for value in _walk_tenant_ids(payload) if value != tenant_id}


def _walk_tenant_ids(payload: Any) -> Iterator[str]:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key == "tenant_id" and isinstance(value, str):
                yield value
            else:
                yield from _walk_tenant_ids(value)
    elif isinstance(payload, list):
        for item in payload:
            yield from _walk_tenant_ids(item)


def _parse_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


def _body_excerpt(response: httpx.Response, limit: int = 400) -> str:
    text = response.text or ""
    return text[:limit]


def mint_token(client: httpx.Client, *, keycloak_url: str, actor: Actor) -> str:
    """Direct password grant, the same shape the other harnesses use."""
    url = f"{keycloak_url}/realms/{actor.realm}/protocol/openid-connect/token"
    form = {
        "grant_type": "password",
        "client_id": actor.client_id,
        "username": actor.username,
        "password": actor.password,
    }
    # A public client has no secret, and Keycloak rejects an empty one rather
    # than ignoring it.
    if actor.client_secret:
        form["client_secret"] = actor.client_secret
    try:
        response = client.post(url, data=form)
    except httpx.HTTPError as exc:
        raise UnreachableTargetError(
            f"Keycloak is unreachable at {url}: {type(exc).__name__}: {exc}"
        ) from exc
    if response.status_code != 200:
        raise UnreachableTargetError(
            f"Keycloak refused the {actor.realm!r} token request with HTTP "
            f"{response.status_code}: {_body_excerpt(response)}"
        )
    token = _parse_json(response)
    if not isinstance(token, dict) or not isinstance(token.get("access_token"), str):
        raise UnreachableTargetError(f"Keycloak token response from {url} carried no access_token")
    return str(token["access_token"])


def assert_denied_by_design(token: str, *, actor: Actor, service_client_id: str) -> None:
    """Refuse to run direction A with an identity the API would let through.

    The signature is not verified, deliberately: this token was minted seconds
    ago by this process, and the only question being asked of it is "am I the
    kind of caller that is supposed to be refused?". The API verifies it for
    real, which is the whole point of asking it.
    """
    claims = _token_claims(token)
    authorized_party = claims.get("azp")
    if authorized_party == service_client_id:
        raise ActorMisconfiguredError(
            f"{actor.username!r} authenticated through client {authorized_party!r}, which is "
            f"the deployment's trusted service client ({service_client_id!r}). That client is "
            "allowed to select any persona by azp alone, so every 403 this canary expects "
            "would come back 200 and prove nothing. Use a client that is not "
            "KEYCLOAK_SERVICE_CLIENT_ID -- in production, movielens-verify -- or pass "
            "--service-client-id if the deployment names a different one."
        )
    realm_access = claims.get("realm_access")
    roles = realm_access.get("roles", []) if isinstance(realm_access, dict) else []
    if PERSONA_ROLE in roles:
        raise ActorMisconfiguredError(
            f"{actor.username!r} holds the {PERSONA_ROLE!r} realm role, so it is entitled to "
            "the persona routes this canary requires it to be refused on. The isolation "
            "identity must be created without that role."
        )


def _token_claims(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 3:
        raise ActorMisconfiguredError(
            "the access token is not a JWS, so the canary cannot confirm its actor is one "
            "the API would deny; refusing to report a result it cannot stand behind"
        )
    payload = parts[1]
    padded = payload + "=" * (-len(payload) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, binascii.Error) as exc:
        raise ActorMisconfiguredError(
            f"the access token's claims could not be read: {exc}"
        ) from exc
    if not isinstance(claims, dict):
        raise ActorMisconfiguredError("the access token's claims are not a JSON object")
    return claims


def run(
    client: httpx.Client,
    *,
    api_url: str,
    keycloak_url: str,
    actor_a: Actor,
    actor_b: Actor,
    persona_user_id: int = TENANT_B_PERSONA_ID,
    foreign_user_id: int = TENANT_A_USER_ID,
    service_client_id: str = DEFAULT_SERVICE_CLIENT_ID,
) -> dict[str, Any]:
    api_url = api_url.rstrip("/")
    keycloak_url = keycloak_url.rstrip("/")
    token_a = mint_token(client, keycloak_url=keycloak_url, actor=actor_a)
    token_b = mint_token(client, keycloak_url=keycloak_url, actor=actor_b)
    assert_denied_by_design(token_a, actor=actor_a, service_client_id=service_client_id)

    guard = check_guard_denies_foreign_actor(
        client,
        api_url=api_url,
        token=token_a,
        persona_user_id=persona_user_id,
    )
    scope = check_no_foreign_tenant_in_payload(
        client,
        api_url=api_url,
        token=token_b,
        tenant_id=actor_b.tenant_id,
        user_id=foreign_user_id,
    )
    failures = [finding for finding in (*guard, *scope) if not finding.passed]
    return {
        "target": api_url,
        "tenant_a": actor_a.tenant_id,
        "tenant_b": actor_b.tenant_id,
        "routes_probed": len(PERSONA_ROUTES),
        "checks": {
            "guard_denies_foreign_actor": [finding.as_dict() for finding in guard],
            "no_foreign_tenant_in_payload": [finding.as_dict() for finding in scope],
        },
        "failures": [finding.as_dict() for finding in failures],
        "passed": not failures,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Remote cross-tenant leakage canary.")
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--keycloak-url", default="http://localhost:8080")
    parser.add_argument("--realm-a", default="default")
    parser.add_argument("--client-id", default="movielens-api")
    parser.add_argument("--client-secret", default="movielens-api-secret-dev-only")
    parser.add_argument("--username", default="alice")
    parser.add_argument("--password", default="alice")
    parser.add_argument("--realm-b", default="demo")
    parser.add_argument("--realm-b-client-id", default=None)
    parser.add_argument("--realm-b-client-secret", default=None)
    parser.add_argument("--realm-b-username", default="demo")
    parser.add_argument("--realm-b-password", default="demo")
    parser.add_argument("--persona-user-id", type=int, default=TENANT_B_PERSONA_ID)
    parser.add_argument("--foreign-user-id", type=int, default=TENANT_A_USER_ID)
    parser.add_argument(
        "--service-client-id",
        default=DEFAULT_SERVICE_CLIENT_ID,
        help=(
            "the deployment's KEYCLOAK_SERVICE_CLIENT_ID. The tenant-A actor must not "
            "authenticate through it, or the persona guard trusts it by azp and this run "
            "proves nothing."
        ),
    )
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args(argv)

    actor_a = Actor(
        realm=str(args.realm_a),
        client_id=str(args.client_id),
        client_secret=str(args.client_secret),
        username=str(args.username),
        password=str(args.password),
    )
    actor_b = Actor(
        realm=str(args.realm_b),
        # A deployment usually issues both harness identities from the same
        # client id, so realm B inherits realm A's client unless told otherwise.
        client_id=str(args.realm_b_client_id or args.client_id),
        client_secret=str(
            args.client_secret if args.realm_b_client_secret is None else args.realm_b_client_secret
        ),
        username=str(args.realm_b_username),
        password=str(args.realm_b_password),
    )

    with httpx.Client(timeout=args.timeout, follow_redirects=False) as client:
        try:
            report = run(
                client,
                api_url=str(args.api_url),
                keycloak_url=str(args.keycloak_url),
                actor_a=actor_a,
                actor_b=actor_b,
                persona_user_id=int(args.persona_user_id),
                foreign_user_id=int(args.foreign_user_id),
                service_client_id=str(args.service_client_id),
            )
        except CanaryError as exc:
            unreachable = isinstance(exc, UnreachableTargetError)
            print(
                json.dumps(
                    {
                        "target": str(args.api_url),
                        "passed": False,
                        "executed": False,
                        "unreachable": unreachable,
                        "error": str(exc),
                    },
                    indent=2,
                )
            )
            banner = "UNREACHABLE TARGET" if unreachable else "MISCONFIGURED ISOLATION ACTOR"
            print(f"\n[tenant-isolation] {banner}: {exc}", file=sys.stderr)
            print(
                "[tenant-isolation] this is a failure, not a skip: nothing about "
                "tenant isolation was proven.",
                file=sys.stderr,
            )
            return 2

    print(json.dumps(report, indent=2))
    _render(report)
    return 0 if report["passed"] else 1


def _render(report: dict[str, Any]) -> None:
    failures = report["failures"]
    print(
        f"\n[tenant-isolation] {report['routes_probed']} persona routes probed as "
        f"tenant {report['tenant_a']!r} against tenant {report['tenant_b']!r} at "
        f"{report['target']}",
        file=sys.stderr,
    )
    if not failures:
        print("[tenant-isolation] PASS — every route denied, no foreign rows", file=sys.stderr)
        return
    for failure in failures:
        print(
            f"[tenant-isolation] FAIL {failure['route']} -> HTTP {failure['status']}: "
            f"{failure['detail']}",
            file=sys.stderr,
        )


if __name__ == "__main__":
    raise SystemExit(main())
