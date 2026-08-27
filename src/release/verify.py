"""Post-deploy verification: prove the deployment, from inside the deployment.

``python -m src.release.verify --all`` runs the rows of the verification matrix
that a private job on the deployment's own network can run, once after a
release and again on a nightly cron:

===== ==========================================================================
V-0   ``/readyz`` answers 200 with no Authorization header — the deploy gate's
      own probe, restated so a cron run also says whether the API is serving.
V-1   The realm's discovery document names exactly the issuer this deployment
      trusts. An inequality here 401s every request while everything reports
      healthy, which is why it is checked before anything authenticates.
V-3   Cold-start handling and learned serving, through the shared demo smoke:
      four personas, Action Fan warm with no seen title recommended back, Cold
      Start with no history but a popularity answer. (Non-negotiable #3.)
V-5   The learned path is genuinely learned. ``serving_policy.learned`` must be
      true for a warm persona — a popularity fallback at HTTP 200 is the
      failure this row exists to refuse.
V-6   Cross-tenant isolation, through the deployment-side canary: an actor from
      another realm is refused on every persona-guarded route, and the actor
      that is entitled to them never receives a row stamped with another
      tenant. (Non-negotiable #9.)
V-7   The write path commits: one idempotent ``PUT`` with ``expected_revision``,
      a replay of the same idempotency key, an immediate authenticated read of
      the committed revision, then a revert. **Eclectic Viewer only** — the
      Cold Start persona's zero-signal state is what the browser E2E set
      depends on, so it is never written to.
V-8   The realm invariants still hold live: registration closed, brute-force
      protection on, the audience mapper present on both browser-facing
      clients, and exactly the expected redirect URIs.
V-9   The audit SLI: one JSON line summarising the last 24 h of
      ``recommendation_audits``, read as ``app_user`` through pgBouncer inside
      a tenant-scoped transaction. (Non-negotiable #8.)
V-12  Artifact provenance: the sidecar reports the versions it loaded, and they
      are the versions the API just served with.
===== ==========================================================================

Not here, on purpose. **V-2** (no public sidecar) is a Railway-API question the
deploy workflow asks. **V-4** and **V-10** are separate harnesses invoked whole
(``synthetic.load.reliability`` and the k6 canary). **V-11** is a Playwright
spec.

Exit codes mirror the isolation canary: 0 every selected check passed, 1 a
check failed, 2 the run could not be performed at all. A run that could not
authenticate is never reported as a pass — a verification job that goes green
because it could not reach the thing it verifies converts an outage into a
green check. The same rule holds one level down: **a selected row that could
not be run is reported as a failed row**, never omitted, so ``VERIFY-OK`` means
every row ran and held rather than every row that happened to run.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import httpx
from sqlalchemy import create_engine, text

from src.config import Settings
from src.release import VERIFY_SENTINEL, VERIFY_SUBSET_SENTINEL
from src.release.bootstrap import PreflightError, check_issuer_equality
from src.serving.orchestration import REASON_LEARNED
from synthetic.smoke.demo import AuthConfig, DemoSmokeError, run_behavior_smoke
from synthetic.tenant_isolation.remote_canary import Actor, CanaryError
from synthetic.tenant_isolation.remote_canary import run as run_isolation_canary

logger = logging.getLogger("release.verify")

# Seeded persona ids. Eclectic Viewer is the write target because the browser
# suite owns the other three differently: Action Fan carries the warm history
# every learned assertion reads, Drama Fan and Cold Start are claimed by
# journeys, and Cold Start's zero-signal state is a precondition the E2E set
# would lose the moment anything wrote to it.
WARM_PERSONA_USER_ID = 900000101
WRITE_PERSONA_USER_ID = 900000103
COLD_START_USER_ID = 900000104
NEVER_WRITE_USER_IDS = frozenset({COLD_START_USER_ID})

LEARNED_POLICY = "item-item-cosine+lightgbm"
AUDIENCE_MAPPER = "oidc-audience-mapper"
AUDIENCE_MAPPER_CONFIG_KEY = "included.client.audience"
BROWSER_FACING_CLIENTS = ("movielens-api", "movielens-web")
CALLBACK_PATH = "/api/auth/callback/keycloak"

CHECK_IDS = ("V-0", "V-1", "V-3", "V-5", "V-6", "V-7", "V-8", "V-9", "V-12")

_DEFAULT_TIMEOUT_SECONDS = 20.0
_DEFAULT_AUDIT_WINDOW_HOURS = 24
# The catalog endpoint's own ceiling (`limit` is ge=1, le=48 in
# src/serving/app.py and in docs/api/openapi.json). Asking for more is a 422,
# not a clamp, and synthetic/load/reliability.py carries the same number under
# the name CATALOG_MAX_LIMIT.
_CATALOG_MAX_LIMIT = 48


class VerifyError(RuntimeError):
    """The verification run itself could not be performed."""


class CheckFailedError(RuntimeError):
    """One matrix row did not hold."""


@dataclass(frozen=True)
class CheckResult:
    id: str
    name: str
    passed: bool
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "passed": self.passed,
            "detail": self.detail,
            "evidence": self.evidence,
        }


@dataclass(frozen=True)
class VerifyConfig:
    """Where this deployment is, and which identities the job may use.

    Every value has an environment variable behind it because the job is
    configured from a variable panel, and a flag in front of it because the
    same module has to be runnable by hand during a rehearsal.
    """

    api_url: str
    web_url: str
    keycloak_url: str
    keycloak_public_base_url: str
    realm: str
    client_id: str
    client_secret: str
    username: str
    password: str
    audience: str
    app_origin: str
    admin_realm: str
    admin_client_id: str
    admin_client_secret: str
    admin_username: str
    admin_password: str
    isolation_realm: str
    isolation_username: str
    isolation_password: str
    service_client_id: str
    warm_user_id: int
    write_user_id: int
    audit_window_hours: int
    timeout: float


def _env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.environ.get(name, "")
        if value.strip():
            return value.strip()
    return default


def config_from_environment(settings: Settings, args: argparse.Namespace) -> VerifyConfig:
    """Resolve the run's configuration: flags first, then §2.9's variables.

    ``KEYCLOAK_URL`` rather than ``KEYCLOAK_BASE_URL`` is deliberate — it is the
    name the deployment contract gives this job and the one the sibling
    harnesses already take — but it falls back to the API's own setting so a
    rehearsal that only configured the service still works.
    """
    return VerifyConfig(
        api_url=(args.api_url or _env("API_URL", default="http://localhost:8000")).rstrip("/"),
        web_url=(args.web_url or _env("WEB_URL", default="http://localhost:3001")).rstrip("/"),
        keycloak_url=(
            args.keycloak_url or _env("KEYCLOAK_URL", default=settings.keycloak_base_url)
        ).rstrip("/"),
        keycloak_public_base_url=(
            args.keycloak_public_url
            or _env("KEYCLOAK_PUBLIC_BASE_URL", default=settings.keycloak_public_base_url)
        ).rstrip("/"),
        realm=args.realm or _env("VERIFY_REALM", default=settings.model_tenant_id),
        client_id=args.client_id or _env("VERIFY_CLIENT_ID", default="movielens-verify"),
        client_secret=args.client_secret or _env("VERIFY_CLIENT_SECRET"),
        username=args.username or _env("VERIFY_USERNAME", default="verify"),
        password=args.password or _env("VERIFY_PASSWORD"),
        audience=settings.keycloak_audience,
        app_origin=(args.app_origin or _env("APP_ORIGIN", "PUBLIC_APP_ORIGIN")).rstrip("/"),
        admin_realm=_env("KEYCLOAK_ADMIN_REALM", default="master"),
        admin_client_id=_env("KEYCLOAK_ADMIN_CLIENT_ID"),
        admin_client_secret=_env("KEYCLOAK_ADMIN_CLIENT_SECRET"),
        admin_username=_env("KEYCLOAK_ADMIN_USERNAME", "KEYCLOAK_ADMIN"),
        admin_password=_env("KEYCLOAK_ADMIN_PASSWORD"),
        isolation_realm=args.isolation_realm or _env("ISOLATION_REALM", default="default"),
        isolation_username=args.isolation_username
        or _env("ISOLATION_USERNAME", default="isolation"),
        isolation_password=args.isolation_password or _env("ISOLATION_PASSWORD"),
        # Whichever client the API trusts by azp alone. The canary refuses to
        # run if the tenant-A actor authenticated through it, so this is read
        # from the deployment's own setting rather than assumed.
        service_client_id=settings.keycloak_service_client_id,
        warm_user_id=args.warm_user_id,
        write_user_id=args.write_user_id,
        audit_window_hours=args.audit_window_hours,
        timeout=args.timeout,
    )


class VerifyRun:
    """One verification pass. Owns the HTTP client, the token and the evidence."""

    def __init__(self, config: VerifyConfig, settings: Settings, client: httpx.Client) -> None:
        if config.write_user_id in NEVER_WRITE_USER_IDS:
            raise VerifyError(
                f"the write-path check refuses user {config.write_user_id}: the Cold Start "
                "persona's zero-signal state is a precondition of the browser E2E set. Use "
                f"the Eclectic Viewer ({WRITE_PERSONA_USER_ID})."
            )
        self.config = config
        self.settings = settings
        self.client = client
        self._token: str | None = None
        self._warm_recommendation: dict[str, Any] | None = None

    # -- identity ---------------------------------------------------------

    @property
    def auth(self) -> AuthConfig:
        return AuthConfig(
            realm=self.config.realm,
            client_id=self.config.client_id,
            client_secret=self.config.client_secret,
            grant_type="password",
            username=self.config.username,
            password=self.config.password,
        )

    @property
    def token(self) -> str:
        """The verify account's access token, minted once for the whole run."""
        if self._token is None:
            # The smoke harness already knows how to mint this; reusing it keeps
            # one definition of "the identity a deployment verifies with".
            from synthetic.smoke.demo import service_access_token

            try:
                self._token = service_access_token(self.client, self.config.keycloak_url, self.auth)
            except DemoSmokeError as exc:
                raise VerifyError(
                    f"the verify identity could not authenticate against realm "
                    f"{self.config.realm!r} at {self.config.keycloak_url}: {exc}"
                ) from exc
        return self._token

    # -- HTTP helpers -----------------------------------------------------

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        merged = {"Authorization": f"Bearer {self.token}"}
        merged.update(headers or {})
        try:
            return self.client.request(
                method, f"{self.config.api_url}{path}", json=json_body, headers=merged
            )
        except httpx.HTTPError as exc:
            raise VerifyError(
                f"{method} {path} could not be reached at {self.config.api_url}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

    def get_json(self, path: str) -> dict[str, Any]:
        response = self.request("GET", path)
        if response.status_code != 200:
            raise CheckFailedError(f"GET {path} answered HTTP {response.status_code}")
        payload = response.json()
        if not isinstance(payload, dict):
            raise CheckFailedError(f"GET {path} did not return a JSON object")
        return payload

    @property
    def warm_recommendation(self) -> dict[str, Any]:
        """The warm persona's recommendation response, fetched once per run."""
        if self._warm_recommendation is None:
            self._warm_recommendation = self.get_json(
                f"/users/{self.config.warm_user_id}/recommendations?limit=10"
            )
        return self._warm_recommendation


# --------------------------------------------------------------------------
# the checks
# --------------------------------------------------------------------------


def check_readyz(run: VerifyRun) -> CheckResult:
    """V-0 — the unauthenticated readiness probe the deploy gate promotes on."""
    url = f"{run.config.api_url}/readyz"
    try:
        response = run.client.get(url)
    except httpx.HTTPError as exc:
        raise CheckFailedError(
            f"/readyz is unreachable at {url}: {type(exc).__name__}: {exc}"
        ) from exc
    payload = _json_object(response)
    if response.status_code != 200 or payload.get("status") != "ready":
        raise CheckFailedError(
            f"/readyz answered HTTP {response.status_code} with {payload!r}; the API cannot "
            "serve an authenticated request"
        )
    return CheckResult(
        "V-0",
        "readiness",
        True,
        "the API is ready and answered without an Authorization header",
        payload,
    )


def _json_object(response: httpx.Response) -> dict[str, Any]:
    """The response body as an object; an unparseable body reads as empty."""
    try:
        payload = response.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def check_issuer(run: VerifyRun) -> CheckResult:
    """V-1 — the realm's discovery document names the issuer the API trusts."""
    try:
        issuer = check_issuer_equality(
            public_base_url=run.config.keycloak_public_base_url,
            realm=run.config.realm,
            timeout=run.config.timeout,
        )
    except PreflightError as exc:
        raise CheckFailedError(str(exc)) from exc
    return CheckResult("V-1", "issuer equality", True, f"issuer is {issuer}", {"issuer": issuer})


def check_smoke(run: VerifyRun) -> CheckResult:
    """V-3 — cold-start handling and learned serving, via the shared harness."""
    try:
        summary = run_behavior_smoke(
            run.client,
            web_url=run.config.web_url,
            api_url=run.config.api_url,
            keycloak_url=run.config.keycloak_url,
            auth=run.auth,
        )
    except DemoSmokeError as exc:
        raise CheckFailedError(str(exc)) from exc
    return CheckResult(
        "V-3",
        "cold-start and learned serving",
        True,
        "four personas present; Action Fan warm with no seen title recommended back; "
        "Cold Start served by popularity with no history",
        dict(summary.__dict__),
    )


def check_learned_serving(run: VerifyRun) -> CheckResult:
    """V-5 — the warm path is learned, not a popularity fallback at HTTP 200."""
    payload = run.warm_recommendation
    policy = payload.get("serving_policy")
    if not isinstance(policy, dict):
        raise CheckFailedError("the recommendation response carries no serving_policy object")
    evidence = {
        "policy": policy.get("name"),
        "learned": policy.get("learned"),
        "reason": policy.get("reason"),
        "positive_signal_count": policy.get("positive_signal_count"),
        "model_version": payload.get("model_version"),
        "items": len(payload.get("items", [])),
    }
    if policy.get("learned") is not True:
        raise CheckFailedError(
            f"user {run.config.warm_user_id} was served by {policy.get('name')!r} with "
            f"learned={policy.get('learned')!r}: {policy.get('reason')!r}. A 200 that "
            "quietly degraded to popularity is exactly what this row refuses."
        )
    if policy.get("name") != LEARNED_POLICY:
        raise CheckFailedError(
            f"learned serving reported policy {policy.get('name')!r}, expected "
            f"{LEARNED_POLICY!r}"
        )
    return CheckResult("V-5", "learned serving", True, f"served by {LEARNED_POLICY}", evidence)


def check_tenant_isolation(run: VerifyRun) -> CheckResult:
    """V-6 — no actor reaches another tenant's rows, asked from outside.

    The harness is ``synthetic.tenant_isolation.remote_canary``, called rather
    than reimplemented: its route table is asserted in CI against the set of
    routes the application actually guards, so an endpoint added later joins
    this row instead of quietly escaping it.

    It needs a second identity — an actor in another realm holding neither
    ``demo-impersonator`` nor the client the API trusts by ``azp`` — and the
    absence of that identity is a failure rather than a skip. Non-negotiable #9
    is the one bug class where "the check did not run" and "the check passed"
    must never look the same in a job summary.
    """
    config = run.config
    if not config.isolation_password:
        raise CheckFailedError(
            "V-6 needs a second tenant's actor and this job holds none, so cross-tenant "
            f"isolation was not proven. Set ISOLATION_PASSWORD for account "
            f"{config.isolation_username!r} in realm {config.isolation_realm!r} — the "
            "account provisioning creates deliberately without demo-impersonator."
        )
    common = {"client_id": config.client_id, "client_secret": config.client_secret}
    try:
        report = run_isolation_canary(
            run.client,
            api_url=config.api_url,
            keycloak_url=config.keycloak_url,
            actor_a=Actor(
                realm=config.isolation_realm,
                username=config.isolation_username,
                password=config.isolation_password,
                **common,
            ),
            actor_b=Actor(
                realm=config.realm,
                username=config.username,
                password=config.password,
                **common,
            ),
            service_client_id=config.service_client_id,
        )
    except CanaryError as exc:
        raise CheckFailedError(
            f"the cross-tenant canary could not be run, which is a failure and not a "
            f"skip: {exc}"
        ) from exc
    if not report["passed"]:
        raise CheckFailedError(
            "cross-tenant leakage canary failed: "
            + "; ".join(
                f"{failure['route']} -> HTTP {failure['status']}: {failure['detail']}"
                for failure in report["failures"]
            )
        )
    return CheckResult(
        "V-6",
        "tenant isolation",
        True,
        f"{report['routes_probed']} persona routes refused for realm "
        f"{report['tenant_a']!r}, and no foreign tenant's rows in realm "
        f"{report['tenant_b']!r}'s payloads",
        {
            "tenant_a": report["tenant_a"],
            "tenant_b": report["tenant_b"],
            "routes_probed": report["routes_probed"],
        },
    )


def check_write_path(run: VerifyRun) -> CheckResult:
    """V-7 — an idempotent write commits, reads back, and is reverted.

    Watchlist rather than watched or dismissed: ADR 0012 makes a watchlist
    entry organizational, so it is the one transition that changes neither the
    positive-signal count the learned path routes on nor the exclusion set
    retrieval filters with. The check therefore proves durability without
    perturbing what the next check measures.
    """
    user_id = run.config.write_user_id
    movie_id, baseline = _pick_watchlistable_movie(run, user_id)
    key = str(uuid4())
    path = f"/users/{user_id}/movies/{movie_id}/watchlist"

    committed = _mutate(run, "PUT", f"{path}?expected_revision={baseline}", key)
    revision = int(committed["state"]["revision"])

    # Everything after the write is collected rather than raised, because the
    # revert has to happen either way: a verification job that leaves its own
    # write behind has changed the thing the next run measures.
    problems: list[str] = []
    try:
        if revision != baseline + 1:
            raise CheckFailedError(
                f"the watchlist write reported revision {revision}, expected {baseline + 1}"
            )
        replay = _mutate(run, "PUT", f"{path}?expected_revision={baseline}", key)
        if replay.get("replayed") is not True:
            raise CheckFailedError(
                "replaying the same Idempotency-Key produced a second mutation rather "
                f"than a replay: {replay!r}"
            )
        if int(replay["state"]["revision"]) != revision:
            raise CheckFailedError("the idempotent replay reported a different revision")
        read_back = run.get_json(f"/users/{user_id}/movies/{movie_id}/state")
        if int(read_back["revision"]) != revision or read_back.get("watchlisted_at") is None:
            raise CheckFailedError(
                f"the immediate read did not return the committed state: {read_back!r}"
            )
    except CheckFailedError as exc:
        problems.append(str(exc))

    # A failing revert raises rather than being collected: a write this job
    # could not undo is the one outcome worth interrupting everything for.
    reverted = _mutate(run, "DELETE", f"{path}?expected_revision={revision}", str(uuid4()))
    if reverted["state"].get("watchlisted_at") is not None:
        problems.append(f"the revert left the watchlist entry standing: {reverted!r}")
    if problems:
        raise CheckFailedError("; ".join(problems))
    return CheckResult(
        "V-7",
        "durable write path",
        True,
        f"movie {movie_id} watchlisted at revision {revision}, replayed, read back, reverted",
        {
            "user_id": user_id,
            "movie_id": movie_id,
            "baseline_revision": baseline,
            "committed_revision": revision,
            "reverted_revision": reverted["state"]["revision"],
        },
    )


def _pick_watchlistable_movie(run: VerifyRun, user_id: int) -> tuple[int, int]:
    """A catalog movie this persona has not watchlisted, and its revision."""
    # 48, not 50: the endpoint declares `limit` as ge=1, le=48 and the committed
    # contract in docs/api/openapi.json says so, so 50 is a 422 before the check
    # begins. That is what it did on this row's first live run.
    page = run.get_json(f"/users/{user_id}/catalog?limit={_CATALOG_MAX_LIMIT}")
    items = page.get("items")
    if not isinstance(items, list):
        raise CheckFailedError("the catalog response carries no items list")
    for item in items:
        if not isinstance(item, dict):
            continue
        state = item.get("state")
        if state is None:
            return int(item["movie_id"]), 0
        if isinstance(state, dict) and state.get("watchlisted_at") is None:
            return int(item["movie_id"]), int(state["revision"])
    raise CheckFailedError(
        f"every movie on the first catalog page is already watchlisted for user {user_id}; "
        "the write-path check has nothing safe to toggle"
    )


def _mutate(run: VerifyRun, method: str, path: str, idempotency_key: str) -> dict[str, Any]:
    response = run.request(method, path, headers={"Idempotency-Key": idempotency_key})
    if response.status_code != 200:
        raise CheckFailedError(
            f"{method} {path} answered HTTP {response.status_code}: {response.text[:300]}"
        )
    payload = response.json()
    if not isinstance(payload, dict) or not isinstance(payload.get("state"), dict):
        raise CheckFailedError(f"{method} {path} did not return a mutation response")
    return payload


def check_realm_invariants(run: VerifyRun) -> CheckResult:
    """V-8 — the realm still is what it was provisioned to be."""
    config = run.config
    if not config.app_origin:
        raise CheckFailedError(
            "V-8 needs the public application origin to know which redirect URIs are the "
            "expected ones. Set APP_ORIGIN (or PUBLIC_APP_ORIGIN) on this job."
        )
    token = _admin_token(run)
    admin = f"{config.keycloak_url}/admin/realms/{config.realm}"
    headers = {"Authorization": f"Bearer {token}"}
    realm = _admin_get(run, admin, headers)
    if not isinstance(realm, dict):
        raise CheckFailedError(f"the admin API did not describe realm {config.realm!r}")

    problems: list[str] = []
    if realm.get("registrationAllowed") is not False:
        problems.append("registrationAllowed is not false")
    if realm.get("bruteForceProtected") is not True:
        problems.append("bruteForceProtected is not true")

    expected_redirect = f"{config.app_origin}{CALLBACK_PATH}"
    clients: dict[str, Any] = {}
    for client_id in BROWSER_FACING_CLIENTS:
        found = _admin_get(run, f"{admin}/clients?clientId={client_id}", headers)
        if not isinstance(found, list) or not found:
            problems.append(f"client {client_id!r} is missing from realm {config.realm!r}")
            continue
        representation = found[0]
        uuid = str(representation.get("id"))
        redirects = sorted(str(uri) for uri in representation.get("redirectUris", []))
        if redirects != [expected_redirect]:
            problems.append(
                f"client {client_id!r} redirect URIs are {redirects} rather than "
                f"[{expected_redirect!r}]"
            )
        mappers = _admin_get(run, f"{admin}/clients/{uuid}/protocol-mappers/models", headers)
        audiences = [
            str(mapper.get("config", {}).get(AUDIENCE_MAPPER_CONFIG_KEY))
            for mapper in (mappers if isinstance(mappers, list) else [])
            if isinstance(mapper, dict) and mapper.get("protocolMapper") == AUDIENCE_MAPPER
        ]
        if config.audience not in audiences:
            problems.append(
                f"client {client_id!r} has no {AUDIENCE_MAPPER} for audience "
                f"{config.audience!r}; every browser token would be rejected by the API"
            )
        clients[client_id] = {"redirect_uris": redirects, "audiences": audiences}

    if problems:
        raise CheckFailedError("; ".join(problems))
    return CheckResult(
        "V-8",
        "realm invariants",
        True,
        "registration closed, brute-force protection on, audience mapper and redirect URIs "
        "as provisioned",
        {
            "realm": config.realm,
            "registrationAllowed": realm.get("registrationAllowed"),
            "bruteForceProtected": realm.get("bruteForceProtected"),
            "clients": clients,
        },
    )


def _admin_token(run: VerifyRun) -> str:
    """Mint an admin-read token, or say exactly which variables are missing.

    The verification account deliberately cannot do this: ``movielens-verify``
    is a confidential client with no service account and no realm-management
    role, which is what keeps a leaked verify credential from being an admin
    credential. So V-8 is the one row that needs its own identity, and its
    absence is a failure rather than a skip.
    """
    config = run.config
    url = f"{config.keycloak_url}/realms/{config.admin_realm}/protocol/openid-connect/token"
    if config.admin_client_id and config.admin_client_secret:
        form = {
            "grant_type": "client_credentials",
            "client_id": config.admin_client_id,
            "client_secret": config.admin_client_secret,
        }
    elif config.admin_username and config.admin_password:
        form = {
            "grant_type": "password",
            "client_id": config.admin_client_id or "admin-cli",
            "username": config.admin_username,
            "password": config.admin_password,
        }
        if config.admin_client_secret:
            form["client_secret"] = config.admin_client_secret
    else:
        raise CheckFailedError(
            "V-8 reads the realm through Keycloak's admin API and this job holds no "
            "admin-read identity. Set KEYCLOAK_ADMIN_CLIENT_ID + "
            "KEYCLOAK_ADMIN_CLIENT_SECRET (a service-account client holding view-realm and "
            "view-clients, which is the least privilege that answers this row), or "
            "KEYCLOAK_ADMIN_USERNAME + KEYCLOAK_ADMIN_PASSWORD."
        )
    try:
        response = run.client.post(url, data=form)
    except httpx.HTTPError as exc:
        raise CheckFailedError(f"Keycloak is unreachable at {url}: {exc}") from exc
    if response.status_code != 200:
        raise CheckFailedError(
            f"the admin-read identity was refused with HTTP {response.status_code}: "
            f"{response.text[:200]}"
        )
    token = response.json().get("access_token")
    if not isinstance(token, str) or not token:
        raise CheckFailedError("the admin token response carried no access_token")
    return token


def _admin_get(run: VerifyRun, url: str, headers: dict[str, str]) -> Any:
    try:
        response = run.client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        raise CheckFailedError(f"the admin API is unreachable at {url}: {exc}") from exc
    if response.status_code != 200:
        raise CheckFailedError(f"GET {url} answered HTTP {response.status_code}")
    return response.json()


_SLI_AGGREGATE = text("""
    SELECT
        count(*)                                                   AS row_count,
        count(*) FILTER (WHERE outcome <> 'success')               AS error_count,
        count(*) FILTER (WHERE reason LIKE :learned_prefix)        AS learned_count,
        percentile_disc(0.5)  WITHIN GROUP (ORDER BY latency_ms)   AS p50_ms,
        percentile_disc(0.95) WITHIN GROUP (ORDER BY latency_ms)   AS p95_ms,
        percentile_disc(0.99) WITHIN GROUP (ORDER BY latency_ms)   AS p99_ms,
        percentile_disc(0.95) WITHIN GROUP (ORDER BY candidate_latency_ms) AS candidate_p95_ms,
        percentile_disc(0.95) WITHIN GROUP (ORDER BY feature_latency_ms)   AS feature_p95_ms,
        percentile_disc(0.95) WITHIN GROUP (ORDER BY ranker_latency_ms)    AS ranker_p95_ms,
        percentile_disc(0.95) WITHIN GROUP (ORDER BY model_latency_ms)     AS model_p95_ms
    FROM recommendation_audits
    WHERE created_at >= now() - (:hours * INTERVAL '1 hour')
""")

_SLI_POLICIES = text("""
    SELECT policy, count(*) AS count
    FROM recommendation_audits
    WHERE created_at >= now() - (:hours * INTERVAL '1 hour')
    GROUP BY policy
    ORDER BY count DESC, policy
""")

_SLI_FALLBACKS = text("""
    SELECT fallback_reason, count(*) AS count
    FROM recommendation_audits
    WHERE created_at >= now() - (:hours * INTERVAL '1 hour')
      AND fallback_reason IS NOT NULL
    GROUP BY fallback_reason
    ORDER BY count DESC, fallback_reason
""")


def audit_sli(settings: Settings, *, tenant_id: str, window_hours: int) -> dict[str, Any]:
    """Roll the last N hours of ``recommendation_audits`` into one SLI record.

    Read as ``app_user`` through pgBouncer inside a tenant-scoped transaction,
    for the same reason the serving path does: the table is FORCE ROW LEVEL
    SECURITY and the rollup should be subject to the isolation it is reporting
    on, not privileged above it.
    """
    engine = create_engine(settings.app_user_database_url, pool_pre_ping=True, future=True)
    parameters = {"hours": window_hours, "learned_prefix": f"{REASON_LEARNED}%"}
    try:
        with engine.begin() as connection:
            connection.execute(text("SET LOCAL app.tenant_id = :tid"), {"tid": tenant_id})
            aggregate = connection.execute(_SLI_AGGREGATE, parameters).mappings().one()
            policies = connection.execute(_SLI_POLICIES, parameters).mappings().all()
            fallbacks = connection.execute(_SLI_FALLBACKS, parameters).mappings().all()
    finally:
        engine.dispose()
    record: dict[str, Any] = {
        "metric": "recommendation_audit_sli",
        "tenant_id": tenant_id,
        "window_hours": window_hours,
    }
    record.update({key: _number(value) for key, value in aggregate.items()})
    record["fallback_count"] = record["row_count"] - record["learned_count"]
    record["policies"] = [{"policy": row["policy"], "count": int(row["count"])} for row in policies]
    record["fallback_reasons"] = [
        {"reason": row["fallback_reason"], "count": int(row["count"])} for row in fallbacks
    ]
    return record


def _number(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    return round(float(value), 3)


def check_audit_sli(run: VerifyRun) -> CheckResult:
    """V-9 — every online prediction is logged, and here is the last day of them."""
    record = audit_sli(
        run.settings,
        tenant_id=run.config.realm,
        window_hours=run.config.audit_window_hours,
    )
    # Emitted on its own line so a log scraper can take the SLI without parsing
    # the report around it.
    print(json.dumps(record, sort_keys=True, default=str))
    if record["row_count"] == 0:
        raise CheckFailedError(
            f"no recommendation audit rows in the last {run.config.audit_window_hours}h for "
            f"tenant {run.config.realm!r}, and this run has just requested recommendations. "
            "Non-negotiable #8 is not holding."
        )
    return CheckResult(
        "V-9",
        "audit SLI",
        True,
        f"{record['row_count']} audited predictions, p99 {record['p99_ms']} ms, "
        f"{record['learned_count']} learned / {record['fallback_count']} fallback",
        record,
    )


def check_artifact_provenance(run: VerifyRun) -> CheckResult:
    """V-12 — the sidecar names the artifacts it loaded, and the API served with them."""
    url = f"{run.settings.model_server_url.rstrip('/')}/healthz"
    try:
        response = run.client.get(url)
    except httpx.HTTPError as exc:
        raise CheckFailedError(
            f"the model sidecar is unreachable at {url}: {type(exc).__name__}: {exc}"
        ) from exc
    payload = _json_object(response)
    if response.status_code != 200 or payload.get("warm") is not True:
        raise CheckFailedError(
            f"the model sidecar answered HTTP {response.status_code} with {payload!r}; it "
            "has not completed the warm-up that re-verifies the bundle's SHA-256s"
        )
    missing = [
        key
        for key in ("candidate_version", "ranker_version", "feature_version")
        if not payload.get(key)
    ]
    if missing:
        raise CheckFailedError(f"the sidecar reported no {', '.join(missing)}")

    served = str(run.warm_recommendation.get("model_version", ""))
    expected = f"{payload['candidate_version']}/{payload['ranker_version']}"
    if served != expected:
        raise CheckFailedError(
            f"the API served model_version {served!r} while the sidecar it is configured "
            f"against reports {expected!r}; the two are not the same deployment"
        )
    return CheckResult(
        "V-12",
        "artifact provenance",
        True,
        f"sidecar warm on {expected}, feature version {payload['feature_version']}",
        {
            "tenant_id": payload.get("tenant_id"),
            "candidate_version": payload.get("candidate_version"),
            "ranker_version": payload.get("ranker_version"),
            "feature_version": payload.get("feature_version"),
            "warmup_ms": payload.get("warmup_ms"),
            "workers": payload.get("workers"),
            "native_threads": payload.get("native_threads"),
            "served_model_version": served,
        },
    )


CHECKS: dict[str, tuple[str, Callable[[VerifyRun], CheckResult]]] = {
    "V-0": ("readiness", check_readyz),
    "V-1": ("issuer equality", check_issuer),
    "V-3": ("cold-start and learned serving", check_smoke),
    "V-5": ("learned serving", check_learned_serving),
    "V-6": ("tenant isolation", check_tenant_isolation),
    "V-7": ("durable write path", check_write_path),
    "V-8": ("realm invariants", check_realm_invariants),
    "V-9": ("audit SLI", check_audit_sli),
    "V-12": ("artifact provenance", check_artifact_provenance),
}

# V-9 counts the audit rows this run's own reads produced, and V-12 compares
# the sidecar against the version the API served, so both have to follow the
# checks that generate that traffic. The order is fixed rather than derived.
CHECK_ORDER = ("V-0", "V-1", "V-8", "V-3", "V-5", "V-7", "V-6", "V-12", "V-9")


def run_checks(run: VerifyRun, selected: Sequence[str]) -> list[CheckResult]:
    results: list[CheckResult] = []
    for check_id in CHECK_ORDER:
        if check_id not in selected:
            continue
        name, check = CHECKS[check_id]
        try:
            result = check(run)
        except CheckFailedError as exc:
            result = CheckResult(check_id, name, False, str(exc))
        logger.info("%s %s: %s", check_id, "PASS" if result.passed else "FAIL", result.detail)
        results.append(result)

    # A selected row that no dispatch table entry could reach is a failed row,
    # not an absent one. Otherwise a check registered in CHECKS but missing from
    # CHECK_ORDER (or a row named on the command line that this build does not
    # implement) would leave VERIFY-OK standing for a matrix that never ran it —
    # the same "green because nothing executed" failure the exit codes exist to
    # refuse, one level down.
    executed = {result.id for result in results}
    for check_id in selected:
        if check_id in executed:
            continue
        name = CHECKS[check_id][0] if check_id in CHECKS else "unknown row"
        detail = (
            f"{check_id} was selected but this build ran nothing for it "
            "(it is absent from CHECK_ORDER, or is not a row this build implements). "
            "Reported as a failure: a row that did not run has proven nothing."
        )
        logger.error("%s FAIL: %s", check_id, detail)
        results.append(CheckResult(check_id, name, False, detail))
    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.release.verify",
        description="Run the post-deploy verification matrix from inside the deployment.",
    )
    parser.add_argument(
        "checks",
        nargs="*",
        metavar="CHECK",
        # Deliberately not `choices=`. argparse validates a `nargs="*"`
        # argument's *default* against choices, so `--all` — which passes no
        # rows at all, and is the command every deployment runs — exited 2 with
        # "invalid choice: []" before this module did anything. The check is
        # made by hand in main() instead, where it can say what this build
        # implements.
        help=f"Matrix rows to run, from {', '.join(CHECK_IDS)}. Omit with --all for every row.",
    )
    parser.add_argument("--all", action="store_true", help="Run every row of the matrix.")
    parser.add_argument("--api-url", default=None)
    parser.add_argument("--web-url", default=None)
    parser.add_argument("--keycloak-url", default=None)
    parser.add_argument("--keycloak-public-url", default=None)
    parser.add_argument("--realm", default=None)
    parser.add_argument("--client-id", default=None)
    parser.add_argument("--client-secret", default=None)
    parser.add_argument("--username", default=None)
    parser.add_argument("--password", default=None)
    parser.add_argument("--app-origin", default=None)
    parser.add_argument("--isolation-realm", default=None)
    parser.add_argument("--isolation-username", default=None)
    parser.add_argument("--isolation-password", default=None)
    parser.add_argument("--warm-user-id", type=int, default=WARM_PERSONA_USER_ID)
    parser.add_argument(
        "--write-user-id",
        type=int,
        default=WRITE_PERSONA_USER_ID,
        help=(
            "V-7's write target. The Cold Start persona is refused: the browser E2E set "
            "depends on its zero-signal state."
        ),
    )
    parser.add_argument("--audit-window-hours", type=int, default=_DEFAULT_AUDIT_WINDOW_HOURS)
    parser.add_argument("--timeout", type=float, default=_DEFAULT_TIMEOUT_SECONDS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )
    parser = build_parser()
    args = parser.parse_args(argv)
    unknown = [check for check in args.checks if check not in CHECK_IDS]
    if unknown:
        parser.error(
            f"unknown matrix row(s): {', '.join(unknown)}. This build implements "
            f"{', '.join(CHECK_IDS)}."
        )
    selected = list(CHECK_IDS) if args.all or not args.checks else list(args.checks)

    try:
        settings = Settings()
        config = config_from_environment(settings, args)
        with httpx.Client(timeout=config.timeout, follow_redirects=False) as client:
            run = VerifyRun(config, settings, client)
            results = run_checks(run, selected)
    except VerifyError as exc:
        print(f"[verify] the run could not be performed: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - Settings guards and driver errors both land here
        print(
            f"[verify] the run could not be performed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2

    failures = [result for result in results if not result.passed]
    report = {
        "target": config.api_url,
        "realm": config.realm,
        "checks": [result.as_dict() for result in results],
        "failures": [result.as_dict() for result in failures],
        "passed": not failures,
    }
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    if failures:
        for failure in failures:
            print(f"[verify] FAIL {failure.id}: {failure.detail}", file=sys.stderr)
        return 1
    # Only a full run earns the sentinel the deploy gate greps for; a
    # deliberately narrowed run says so in its own words instead.
    print(VERIFY_SENTINEL if len(selected) == len(CHECK_IDS) else VERIFY_SUBSET_SENTINEL)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
