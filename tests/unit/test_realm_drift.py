"""Realm drift: what the seeds claim, what Keycloak realizes, and what production keeps.

ADR 0007's risk section promised a realm-drift check and no job has ever run one, so the
committed realm JSON has been an unverified claim for the whole of Phase 3. This module is
that check, in two layers answering two different questions.

*Static* — always runs, needs nothing but the repository. The production templates under
``infra/keycloak/realms/prod/`` keep every security-relevant setting the dev seeds carry, and
every place the two differ is a tightening rather than a drift. The dev seeds are the only
realm configuration this project has actually run against; production is allowed to be
stricter than that baseline and nothing else. The interesting failure is not a changed value
but a *dropped* one: a setting that exists in the seed and simply is not in the production
template inherits whatever Keycloak's default happens to be, silently.

*Live* — runs when ``REALM_EXPORT_DIR`` names a directory of ``kc.sh export`` output. Import
is lossy in the one direction that matters: Keycloak drops a property it does not recognise
without complaining, so a seed can claim a setting the server never applied and every local
stack, every CI job and every reviewer would agree the file says the right thing. Diffing an
export of the realms the server actually built against the seeds it built them from is the
only thing that catches that.

The live layer refuses to skip when ``REQUIRE_REALM_EXPORT`` is set, for the reason W10 gave
the tenant-isolation conftest: in a job summary a skip and a pass are the same green tick.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, NoReturn

import pytest

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_KEYCLOAK_DIR = _REPOSITORY_ROOT / "infra" / "keycloak"
_SEED_DIR = _KEYCLOAK_DIR / "realms"
_PROD_DIR = _SEED_DIR / "prod"
_PROD_CLIENT_DIR = _PROD_DIR / "clients"
_PROVISION = _KEYCLOAK_DIR / "provision.sh"

_REALMS = ("default", "demo")

#: Where a ``kc.sh export --dir`` landed, and the switch that makes its absence a failure
#: rather than a skip. The CI job sets both; a developer running ``pytest tests/unit/`` sets
#: neither and gets the static layer alone.
EXPORT_DIR_ENV = "REALM_EXPORT_DIR"
REQUIRE_EXPORT_ENV = "REQUIRE_REALM_EXPORT"
_TRUTHY = frozenset({"1", "true", "yes", "on"})

# Realm-level keys that hold objects rather than settings. The live layer compares every
# other key the seed carries, so anything added to a seed is compared without being listed
# here twice; these are the ones with their own assertions below.
_REALM_COLLECTIONS = frozenset({"clients", "users", "roles", "groups", "identityProviders"})

# What production is allowed to do with each security-relevant realm setting.
_MUST_STAY_OFF = ("registrationAllowed", "resetPasswordAllowed", "editUsernameAllowed")
_MUST_STAY_ON = ("enabled", "bruteForceProtected")
_MUST_NOT_LENGTHEN = ("accessTokenLifespan", "ssoSessionIdleTimeout", "ssoSessionMaxLifespan")
# Settings that carry the same meaning in both environments: a production deployment that
# quietly allowed duplicate emails or email login where dev does not is a behaviour change,
# not a hardening, and belongs in a reviewed template edit.
_MUST_MATCH = ("duplicateEmailsAllowed", "loginWithEmailAllowed")
# Keycloak's own scale, lowest first, so "at least as strict as the seed" is an index
# comparison. "external" is the deployed value on purpose (W5): TLS terminates at the edge
# and the BFF's token exchange is routed to the internal origin over plain http.
_SSL_STRICTNESS = ("none", "external", "all")

_SECURITY_RELEVANT_REALM_KEYS = (
    *_MUST_STAY_OFF,
    *_MUST_STAY_ON,
    *_MUST_NOT_LENGTHEN,
    *_MUST_MATCH,
    "sslRequired",
)

# Client flags that decide which tokens a client can mint, and how it proves who it is.
_CLIENT_IDENTITY_FLAGS = ("protocol", "publicClient", "bearerOnly", "clientAuthenticatorType")
_CLIENT_GRANT_FLAGS = (
    "standardFlowEnabled",
    "directAccessGrantsEnabled",
    "serviceAccountsEnabled",
)
# Client attributes whose value is a security decision rather than a preference.
_PKCE_ATTRIBUTE = "pkce.code.challenge.method"
_TOKEN_LIFESPAN_ATTRIBUTE = "access.token.lifespan"

_AUDIENCE_MAPPER = "oidc-audience-mapper"


def _load(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        loaded: dict[str, Any] = json.load(handle)
    return loaded


def _seed(realm: str) -> dict[str, Any]:
    return _load(_SEED_DIR / f"{realm}-realm.json")


def _prod_realm(realm: str) -> dict[str, Any]:
    return _load(_PROD_DIR / f"{realm}-realm.json")


def _seed_clients(realm: str) -> dict[str, dict[str, Any]]:
    return {client["clientId"]: client for client in _seed(realm).get("clients", [])}


def _prod_clients() -> dict[str, dict[str, Any]]:
    """Production clients are one file each, shared by both realms (W5)."""
    clients = {}
    for path in sorted(_PROD_CLIENT_DIR.glob("*.json")):
        template = _load(path)
        client_id = template.get("clientId")
        if client_id is not None:  # audience-mapper.json is a mapper, not a client
            clients[client_id] = template
    return clients


def _seeded_client_ids() -> list[str]:
    ids: set[str] = set()
    for realm in _REALMS:
        ids.update(_seed_clients(realm))
    return sorted(ids)


def _audience_mappers(client: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        mapper
        for mapper in client.get("protocolMappers", [])
        if mapper.get("protocolMapper") == _AUDIENCE_MAPPER
    ]


def _grants(client: dict[str, Any], flag: str) -> bool:
    """A grant flag Keycloak defaults to false when a template omits it."""
    return bool(client.get(flag, False))


def _export_required() -> bool:
    return os.environ.get(REQUIRE_EXPORT_ENV, "").strip().lower() in _TRUTHY


def _no_export(reason: str) -> NoReturn:
    if _export_required():
        pytest.fail(
            f"{REQUIRE_EXPORT_ENV} was set, so the live realm comparison must run: {reason}. "
            "Skipping here would report a pass for a gate that never executed — start "
            "Keycloak, run `kc.sh export --dir <dir> --users skip --http-management-port 9001` "
            f"inside it, and point {EXPORT_DIR_ENV} at the result.",
            pytrace=False,
        )
    pytest.skip(f"{reason}; set {EXPORT_DIR_ENV} to compare against a live Keycloak")


def _exported(realm: str) -> dict[str, Any]:
    directory = os.environ.get(EXPORT_DIR_ENV, "").strip()
    if not directory:
        _no_export(f"{EXPORT_DIR_ENV} is not set")
    path = Path(directory) / f"{realm}-realm.json"
    if not path.is_file():
        _no_export(f"no export of realm '{realm}' at {path}")
    return _load(path)


def _exported_clients(realm: str) -> dict[str, dict[str, Any]]:
    return {client["clientId"]: client for client in _exported(realm).get("clients", [])}


# --- Static layer: production keeps what the seeds carry --------------------------------


@pytest.mark.parametrize("realm", _REALMS)
def test_production_templates_keep_every_seeded_security_setting(realm: str) -> None:
    seed = _seed(realm)
    template = _prod_realm(realm)

    assert template["realm"] == seed["realm"]

    missing = [key for key in _SECURITY_RELEVANT_REALM_KEYS if key in seed and key not in template]
    # An omitted setting is the drift worth catching: it does not read as a change in
    # review, and production silently inherits Keycloak's default for it.
    assert not missing, (
        f"the production template for realm '{realm}' drops security settings the dev seed "
        f"carries: {', '.join(missing)}"
    )


@pytest.mark.parametrize("realm", _REALMS)
def test_production_realms_only_tighten_the_seeded_settings(realm: str) -> None:
    seed = _seed(realm)
    template = _prod_realm(realm)

    for key in _MUST_STAY_OFF:
        assert template[key] is False, f"realm '{realm}': {key} must stay off in production"

    for key in _MUST_STAY_ON:
        assert template[key] is True, f"realm '{realm}': {key} must stay on in production"

    for key in _MUST_NOT_LENGTHEN:
        assert template[key] <= seed[key], (
            f"realm '{realm}': production extends {key} from {seed[key]} to {template[key]}; "
            "a production session may be shorter-lived than dev's, never longer"
        )

    for key in _MUST_MATCH:
        assert template[key] == seed[key], (
            f"realm '{realm}': {key} differs between the dev seed and production "
            f"({seed[key]} vs {template[key]}), which changes behaviour rather than hardening it"
        )

    seeded_tls = _SSL_STRICTNESS.index(seed["sslRequired"])
    production_tls = _SSL_STRICTNESS.index(template["sslRequired"])
    assert production_tls >= seeded_tls, (
        f"realm '{realm}': production relaxes sslRequired from {seed['sslRequired']} to "
        f"{template['sslRequired']}"
    )


def test_production_declares_every_client_the_seeds_declare() -> None:
    missing = sorted(set(_seeded_client_ids()) - set(_prod_clients()))
    assert not missing, (
        "the production client templates are missing clients the dev seeds declare: "
        f"{', '.join(missing)}"
    )


@pytest.mark.parametrize("client_id", _seeded_client_ids())
def test_production_clients_only_tighten_the_seeded_grants(client_id: str) -> None:
    template = _prod_clients()[client_id]

    for realm in _REALMS:
        seeded = _seed_clients(realm).get(client_id)
        if seeded is None:
            continue

        for flag in _CLIENT_IDENTITY_FLAGS:
            if flag in seeded:
                assert template.get(flag) == seeded[flag], (
                    f"client '{client_id}': {flag} differs from the dev seed "
                    f"({seeded[flag]} vs {template.get(flag)}). This is what a client *is*, "
                    "not how much it is allowed to do"
                )

        for flag in _CLIENT_GRANT_FLAGS:
            if _grants(seeded, flag):
                continue
            assert not _grants(
                template, flag
            ), f"client '{client_id}': production enables {flag} where the dev seed does not"

        seeded_attributes = seeded.get("attributes", {})
        template_attributes = template.get("attributes", {})

        if _PKCE_ATTRIBUTE in seeded_attributes:
            assert template_attributes.get(_PKCE_ATTRIBUTE) == seeded_attributes[_PKCE_ATTRIBUTE], (
                f"client '{client_id}': production drops or weakens PKCE, which is the only "
                "thing binding an authorization code to the browser that requested it"
            )

        if _TOKEN_LIFESPAN_ATTRIBUTE in seeded_attributes:
            seeded_lifespan = int(seeded_attributes[_TOKEN_LIFESPAN_ATTRIBUTE])
            template_lifespan = int(template_attributes.get(_TOKEN_LIFESPAN_ATTRIBUTE, 0))
            assert 0 < template_lifespan <= seeded_lifespan, (
                f"client '{client_id}': production access tokens live for "
                f"{template_lifespan}s against the seed's {seeded_lifespan}s"
            )


def test_production_attaches_the_audience_mapper_the_seeds_prove() -> None:
    """Every seeded client carries the same audience mapper; production declares it once."""
    production = _load(_PROD_CLIENT_DIR / "audience-mapper.json")
    assert production["protocolMapper"] == _AUDIENCE_MAPPER

    for realm in _REALMS:
        for client_id, seeded in _seed_clients(realm).items():
            mappers = _audience_mappers(seeded)
            assert mappers, (
                f"realm '{realm}' client '{client_id}' carries no audience mapper — without "
                "one the API rejects every token this client mints (aud=movielens-api)"
            )
            for mapper in mappers:
                assert mapper["config"] == production["config"], (
                    f"realm '{realm}' client '{client_id}': the audience mapper production "
                    "attaches differs from the one the dev seeds have been proving works"
                )


@pytest.mark.parametrize("realm", _REALMS)
def test_provisioning_names_every_seeded_realm_role(realm: str) -> None:
    """A role the seeds declare must at least be known to the production provisioner.

    Deliberately a weak statement: provision.sh creates realm roles with `-s name=...`
    rather than declaring them in the templates (a realm PUT ignores nested roles), so
    there is no structured place to compare against. What this catches is the real
    failure — a third role added to the dev seeds and never provisioned anywhere else.
    """
    script = _PROVISION.read_text(encoding="utf-8")
    for role in _seed(realm).get("roles", {}).get("realm", []):
        assert role["name"] in script, (
            f"realm role '{role['name']}' is seeded in dev but never named by "
            f"{_PROVISION.relative_to(_REPOSITORY_ROOT)}"
        )


# --- Live layer: the server realized what the seeds describe ----------------------------


@pytest.mark.parametrize("realm", _REALMS)
def test_exported_realm_matches_the_committed_seed(realm: str) -> None:
    seed = _seed(realm)
    exported = _exported(realm)

    drifted = {
        key: (value, exported.get(key))
        for key, value in seed.items()
        if key not in _REALM_COLLECTIONS and exported.get(key) != value
    }
    assert not drifted, (
        f"realm '{realm}' as Keycloak built it differs from the seed it was built from: "
        + ", ".join(
            f"{key}: seed={seeded!r} live={live!r}" for key, (seeded, live) in drifted.items()
        )
    )


@pytest.mark.parametrize("realm", _REALMS)
def test_exported_clients_match_the_committed_seed(realm: str) -> None:
    exported = _exported_clients(realm)

    for client_id, seeded in _seed_clients(realm).items():
        assert client_id in exported, f"realm '{realm}' has no client '{client_id}'"
        live = exported[client_id]

        for flag in (*_CLIENT_IDENTITY_FLAGS, *_CLIENT_GRANT_FLAGS, "enabled"):
            if flag in seeded:
                assert live.get(flag) == seeded[flag], (
                    f"realm '{realm}' client '{client_id}': {flag} is {live.get(flag)!r} live "
                    f"but {seeded[flag]!r} in the seed"
                )

        for key in ("redirectUris", "webOrigins"):
            if key in seeded:
                assert set(live.get(key, [])) == set(
                    seeded[key]
                ), f"realm '{realm}' client '{client_id}': {key} drifted from the seed"

        # Keycloak adds attributes of its own, so only the seeded keys are compared.
        for key, value in seeded.get("attributes", {}).items():
            assert live.get("attributes", {}).get(key) == value, (
                f"realm '{realm}' client '{client_id}': attribute {key} is "
                f"{live.get('attributes', {}).get(key)!r} live but {value!r} in the seed"
            )

        seeded_mappers = {mapper["name"]: mapper for mapper in _audience_mappers(seeded)}
        live_mappers = {mapper["name"]: mapper for mapper in _audience_mappers(live)}
        for name, mapper in seeded_mappers.items():
            assert name in live_mappers, (
                f"realm '{realm}' client '{client_id}': the audience mapper '{name}' the seed "
                "declares was not applied — every token this client mints would miss "
                "aud=movielens-api and the API would reject it"
            )
            # Subset rather than equality: Keycloak fills in defaults the seed does not
            # mention (it adds `userinfo.token.claim` to every audience mapper), and a
            # default the server chose is not drift. What the seed states, it must get.
            live_config = live_mappers[name].get("config", {})
            drifted = {
                key: (value, live_config.get(key))
                for key, value in mapper["config"].items()
                if live_config.get(key) != value
            }
            assert not drifted, (
                f"realm '{realm}' client '{client_id}': audience mapper '{name}' was applied "
                "with a different configuration than the seed asked for: "
                + ", ".join(
                    f"{key}: seed={seeded!r} live={live!r}"
                    for key, (seeded, live) in drifted.items()
                )
            )


@pytest.mark.parametrize("realm", _REALMS)
def test_exported_realm_declares_every_seeded_role(realm: str) -> None:
    seeded = {role["name"] for role in _seed(realm).get("roles", {}).get("realm", [])}
    live = {role["name"] for role in _exported(realm).get("roles", {}).get("realm", [])}

    missing = sorted(seeded - live)
    assert not missing, (
        f"realm '{realm}' is missing seeded realm roles: {', '.join(missing)}. "
        "ADR 0012's demo-impersonator gate is one of these"
    )
