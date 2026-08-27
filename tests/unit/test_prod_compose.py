"""Structural gates on the production stack.

``docker compose config`` proves the file parses and that every variable it
interpolates has a value. It cannot prove the things this stack has to be: that
no dev-only switch survived into it, that nothing but the TLS edge is reachable
from the host, that the services which construct ``Settings()`` all say
``production``, that every image is the one CI published rather than one the
box built for itself, and that the variable contract and its example file are
still the same contract. Those are the claims the deployment rests on, so they
are asserted here rather than remembered.

Nothing in this module starts a container or reads the network.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PATH = REPO_ROOT / "docker-compose.prod.yml"
ENV_EXAMPLE_PATH = REPO_ROOT / "infra" / "deploy" / "production.env.example"
CADDYFILE_PATH = REPO_ROOT / "infra" / "edge" / "Caddyfile"
MAKEFILE_PATH = REPO_ROOT / "Makefile"

# Everything that runs between deploys. The jobs below run and exit.
LONG_LIVED_SERVICES = frozenset(
    {
        "postgres-app",
        "postgres-keycloak",
        "redis",
        "pgbouncer",
        "keycloak",
        "api",
        "model-server",
        "feature-server",
        "web",
        "edge",
    }
)
JOB_SERVICES = frozenset(
    {
        "postgres-provision",
        "release",
        "materialize",
        "keycloak-provision",
        "verify",
        "canary",
        "backup",
        "loadcheck",
        "rollback-rehearsal",
    }
)

# Every container that constructs Settings(), and therefore every container
# whose environment the three credential guards apply to.
SETTINGS_BUILDING_SERVICES = frozenset(
    {
        "api",
        "model-server",
        "release",
        "materialize",
        "verify",
        "canary",
        "rollback-rehearsal",
    }
)

# The switches whose only safe production value is absence. "false" is not
# absence: a variable that is present is a variable somebody can flip.
FORBIDDEN_VARIABLES = (
    "DEV_AUTH_BYPASS",
    "DEV_BYPASS_TENANT",
    "DEV_BYPASS_USER",
    "MOVIELENS_UI_FIXTURE_MODE",
    "POSTGRES_HOST_AUTH_METHOD",
)

# Section 2.11's inventory: every credential that is checked into the public
# tree and must never be the value a deployment starts from.
DEV_CREDENTIAL_LITERALS = frozenset(
    {
        "recsys",
        "app_user",
        "admin_user",
        "migrator",
        "pgbouncer_admin",
        "pgbouncer_auth",
        "keycloak",
        "admin",
        "demo",
        "alice",
        "dev-model-server-token",
        "movielens-api-secret-dev-only",
        "movielens-demo-auth-secret-change-outside-local-dev",
    }
)

_VARIABLE_REFERENCE = re.compile(
    r"(?<!\$)\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?P<modifier>:?[-?][^}]*)?\}"
)


def _load_compose() -> dict[str, Any]:
    with COMPOSE_PATH.open(encoding="utf-8") as handle:
        document: dict[str, Any] = yaml.safe_load(handle)
    return document


COMPOSE = _load_compose()
COMPOSE_TEXT = COMPOSE_PATH.read_text(encoding="utf-8")
SERVICES: dict[str, dict[str, Any]] = COMPOSE["services"]


def _environment(service: str) -> dict[str, str]:
    return {str(k): str(v) for k, v in SERVICES[service].get("environment", {}).items()}


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        values[name.strip()] = value.strip()
    return values


ENV_EXAMPLE = _parse_env_file(ENV_EXAMPLE_PATH)


def test_the_stack_declares_every_service_in_the_deployment_topology() -> None:
    assert set(SERVICES) == LONG_LIVED_SERVICES | JOB_SERVICES


def test_only_the_edge_is_reachable_from_the_host() -> None:
    """On a single box, a published port is a port on the public internet.

    ufw is the second line of defence and this is the first: nothing but the
    edge is reachable from outside the Compose network. It is also what keeps
    the demo stack and this one from colliding on a host port.
    """
    published = {name: body["ports"] for name, body in SERVICES.items() if body.get("ports")}
    assert set(published) == {"edge"}


def test_the_edge_answers_to_the_public_hostnames_inside_the_network() -> None:
    """Without the aliases the public issuer is unreachable from a job.

    app.localtest.me resolves to 127.0.0.1, which inside a container is the
    container itself -- so the release job's issuer-equality preflight would
    fail against its own loopback rather than against Keycloak.
    """
    aliases = SERVICES["edge"]["networks"]["default"]["aliases"]
    assert "${PUBLIC_APP_HOST:-app.localtest.me}" in aliases
    assert "${PUBLIC_AUTH_HOST:-auth.localtest.me}" in aliases


def test_no_job_starts_on_up() -> None:
    for name in JOB_SERVICES:
        assert SERVICES[name].get("profiles") == ["jobs"], name
    for name in LONG_LIVED_SERVICES:
        assert "profiles" not in SERVICES[name], name


@pytest.mark.parametrize("variable", FORBIDDEN_VARIABLES)
def test_no_dev_only_switch_is_set_on_any_service(variable: str) -> None:
    """Absent, not "false".

    Settings() refuses to construct with the auth bypass on outside dev, but
    the stronger property is that the variable is not there at all: a value
    somebody can read in a panel is a value somebody can change.
    """
    for name, body in SERVICES.items():
        assert variable not in _environment(name), name
        rendered = " ".join(str(part) for part in body.get("command", []))
        assert variable not in rendered, name


@pytest.mark.parametrize("service", sorted(SETTINGS_BUILDING_SERVICES))
def test_every_settings_building_service_declares_production(service: str) -> None:
    assert _environment(service)["ENVIRONMENT"] == "production"


@pytest.mark.parametrize("service", sorted(SETTINGS_BUILDING_SERVICES))
def test_every_settings_building_service_carries_the_guarded_credentials(service: str) -> None:
    """Settings() refuses to construct without these outside dev.

    The model-server token and the pgBouncer admin password are checked into a
    public repository, so the constructor rejects their defaults anywhere but
    dev -- including in containers that never speak to the sidecar or open the
    admin console.
    """
    environment = _environment(service)
    assert "${MODEL_SERVER_AUTH_TOKEN" in environment["MODEL_SERVER_AUTH_TOKEN"]
    if service in {"api", "verify", "canary", "rollback-rehearsal", "release"}:
        assert "${PGBOUNCER_ADMIN_PASSWORD" in environment["PGBOUNCER_ADMIN_PASSWORD"]


def test_the_api_serves_through_the_entrypoint_mode_and_not_a_uvicorn_line() -> None:
    """`serve`, and the worker count as a variable rather than a literal.

    Spelling the uvicorn line out here renders the identical command, but it
    lands in the entrypoint's `exec "$@"` fall-through rather than its `serve`
    branch -- skipping the settings preflight that turns a bad variable into an
    exit code instead of workers respawning forever inside a container that
    stays "running". PORT and API_WORKERS are what drive the shape, so they are
    asserted here: the command line no longer states it.
    """
    assert SERVICES["api"]["command"] == ["serve", "--no-access-log"]
    environment = _environment("api")
    assert environment["API_WORKERS"] == "${API_WORKERS:-2}"
    assert environment["PORT"] == "8000"
    entrypoint = (REPO_ROOT / "infra" / "api" / "entrypoint.sh").read_text(encoding="utf-8")
    assert "${API_WORKERS:-4}" in entrypoint, "the entrypoint no longer reads API_WORKERS"


def test_the_worker_counts_are_sized_for_the_box_and_stated_once() -> None:
    """Two vCPUs, so two workers -- and the load job has to agree with the API.

    The k6 warm-up sizes itself from API_WORKERS: a warm-up that primes fewer
    processes than exist leaves cold ones inside the measured window, which is
    the failure ADR 0010's hardening note was written about. One variable, read
    by both services, is what stops the two drifting.
    """
    assert _environment("api")["API_WORKERS"] == _environment("loadcheck")["API_WORKERS"]
    for service in ("api", "loadcheck"):
        assert _environment(service)["API_WORKERS"].endswith(":-2}"), service
    assert _environment("model-server")["MODEL_SERVER_WORKERS"].endswith(":-2}")
    for name in ("API_WORKERS", "MODEL_SERVER_WORKERS"):
        assert name in ENV_EXAMPLE, f"{name} is the box's sizing; it belongs in the example"


def test_the_api_keeps_the_model_server_timeout_it_was_measured_with() -> None:
    """0.5 s is a decision, not a default.

    The release handoff forbids inflating it to paper over cold workers, and
    the sidecar's lifespan warm-up is what removed the reason anyone would.
    """
    assert _environment("api")["MODEL_SERVER_TIMEOUT_SECONDS"] == "0.5"


def test_the_api_reaches_postgres_only_through_pgbouncer_as_app_user() -> None:
    environment = _environment("api")
    assert environment["APP_USER_DB_HOST"] == "pgbouncer"
    assert environment["APP_USER_DB_PORT"] == "6432"
    assert environment["APP_USER_DB_NAME"] == "movielens_app"
    assert environment["APP_USER_DB_USER"] == "app_user"
    # The BYPASSRLS engine is gone, the migrator DSN lives only on release, and
    # no TMDB route exists at runtime. Each absence is a decision.
    absent = [
        name
        for name in environment
        if name.startswith(("ADMIN_USER_DB_", "POSTGRES_")) or name == "TMDB_READ_ACCESS_TOKEN"
    ]
    assert absent == []


def test_the_authorized_parties_list_is_json_and_admits_the_service_client() -> None:
    """A variable panel is exactly where somebody types the comma.

    pydantic-settings parses this field as JSON; the CSV form raises
    SettingsError and crash-loops the container with no partial mode.
    """
    environment = _environment("api")
    parties = json.loads(environment["KEYCLOAK_AUTHORIZED_PARTIES"])
    assert isinstance(parties, list)
    assert environment["KEYCLOAK_SERVICE_CLIENT_ID"] in parties
    # Deliberately not the verify client: RequestPrincipal.can_access_demo_personas
    # trusts the service client by azp alone, and the isolation canary
    # authenticates through movielens-verify.
    assert environment["KEYCLOAK_SERVICE_CLIENT_ID"] != "movielens-verify"
    assert "movielens-verify" in parties


def test_the_model_server_repeats_the_thread_pins_the_image_bakes() -> None:
    """ADR 0010 calls these a serving invariant, not a test convenience.

    Unpinned, the measured p99 was 903.64 ms at 0% CPU steal; pinning these
    four alone brought it to 48.99 ms. The image bakes them; restating them
    here is what makes the deployed topology auditable from this file.
    """
    environment = _environment("model-server")
    for name in (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
    ):
        assert environment[name] == "1", name
    # The worker count is on the command line, where the process cannot read it
    # back, so /healthz reports null without this. The two must agree.
    command = SERVICES["model-server"]["command"]
    assert environment["MODEL_SERVER_WORKERS"] == command[command.index("--workers") + 1]


def test_neither_sidecar_takes_a_volume() -> None:
    """The bundle and the registry are baked, which is the whole point.

    A serving artifact in a volume outlives the image that was tested with it,
    so a rollback would move the code back and leave the model where it was.
    Baked, a model rollback is an image rollback.
    """
    assert "volumes" not in SERVICES["model-server"]
    assert "volumes" not in SERVICES["feature-server"]


def test_no_long_lived_service_but_the_edge_mounts_the_repository() -> None:
    """The box has a checkout, and the services still must not read from it.

    An image is pinned by tag and a bind mount is not, so a service that read
    its configuration, registry or model bundle off the disk would not roll
    back with everything else -- `deploy.sh --rollback` moves images, not the
    working tree. The edge is the exception because its Caddyfile is the one
    piece of configuration that has no image of its own.
    """
    for name in sorted(LONG_LIVED_SERVICES - {"edge"}):
        mounts = [str(mount) for mount in SERVICES[name].get("volumes", [])]
        assert [mount for mount in mounts if mount.startswith(("./", "../", "/"))] == [], name


def test_redis_is_configured_as_a_store_and_not_as_a_cache() -> None:
    """An eviction here degrades every ranking score with no failure anywhere.

    The feature views declare 3650-day TTLs and a missing feature reads back as
    0.0 rather than raising, so noeviction is what stops the online store
    quietly emptying.
    """
    command = " ".join(SERVICES["redis"]["command"])
    assert "--maxmemory-policy noeviction" in command
    assert "--appendonly yes" in command
    assert "--requirepass" in command


def test_the_release_job_migrates_as_migrator_and_not_as_the_superuser() -> None:
    """Ownership is what makes FORCE ROW LEVEL SECURITY and the 0010 backfill work.

    Because create_tables() runs first as migrator, migrator owns every base
    table. This is achieved purely by the variable below -- there is no code
    path that names the role.
    """
    environment = _environment("release")
    assert environment["POSTGRES_USER"] == "migrator"
    assert SERVICES["release"]["command"] == ["bootstrap", "all"]


def test_the_materialize_fence_reads_its_own_head_rather_than_being_told() -> None:
    """The features image carries alembic.ini + alembic/ for exactly this.

    It used to be told, through a hand-maintained ``RELEASE_EXPECTED_REVISION``
    literal, because the image shipped no Alembic. A literal in a variable panel
    is only ever as correct as the last person to remember it, and its failure
    mode is the expensive one: the model-server's pre-deploy waits out its whole
    300 s and fails the deploy. Reading the image's own migration set is also
    what lets the fence recognise a database that is *ahead* — the rollback
    path, where the release job correctly applied nothing.
    """
    assert "--wait-for-schema" in SERVICES["materialize"]["command"]
    for name in SERVICES:
        assert "RELEASE_EXPECTED_REVISION" not in _environment(name), name
    dockerfile = (REPO_ROOT / "infra" / "features" / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY alembic.ini ./" in dockerfile
    assert "COPY alembic ./alembic" in dockerfile
    requirements = (REPO_ROOT / "infra" / "features" / "requirements.txt").read_text(
        encoding="utf-8"
    )
    assert re.search(
        r"^alembic==", requirements, re.MULTILINE
    ), "the features image needs the alembic package as well as the scripts"


def test_the_verify_job_can_reach_every_row_it_declares() -> None:
    """Three variables, each of which turns a row into a failure when absent.

    None of them fails loudly at boot: the job runs, the row reports that it
    could not be performed, and the deploy gate refuses the release. That is the
    right direction, and it is still cheaper to have them here.
    """
    environment = _environment("verify")
    # V-12 reads the sidecar's own /healthz; the setting's default is localhost,
    # which inside this job is this job.
    assert environment["MODEL_SERVER_URL"] == "http://model-server:6570"
    # V-8 cannot know which redirect URIs are the expected ones without it.
    assert environment["APP_ORIGIN"] == "https://${PUBLIC_APP_HOST:-app.localtest.me}"
    # V-8's admin-read identity: the human admin today, a service-account client
    # whenever one exists, and the job prefers the client when both are set.
    assert environment["KEYCLOAK_ADMIN_USERNAME"].startswith("${KC_HUMAN_ADMIN_USERNAME")
    assert environment["KEYCLOAK_ADMIN_PASSWORD"].startswith("${KC_HUMAN_ADMIN_PASSWORD")
    assert environment["KEYCLOAK_ADMIN_CLIENT_ID"] == "${KEYCLOAK_ADMIN_CLIENT_ID:-}"
    assert environment["KEYCLOAK_ADMIN_CLIENT_SECRET"] == "${KEYCLOAK_ADMIN_CLIENT_SECRET:-}"
    # V-6's tenant-A actor, which the provisioning creates deliberately without
    # demo-impersonator.
    assert environment["ISOLATION_REALM"] == "default"
    assert environment["ISOLATION_USERNAME"] == "isolation"
    assert environment["ISOLATION_PASSWORD"].startswith("${ISOLATION_PASSWORD")


def test_the_isolation_canary_travels_inside_the_image() -> None:
    """It lives under synthetic/, which infra/api/Dockerfile copies.

    While it lived under tests/, which the image does not copy, the verify job
    could only report that it was unable to run the one check standing behind
    the project's highest-severity bug class, and this rehearsal stack had to
    bind-mount the repository to run it at all.
    """
    command = " ".join(str(part) for part in SERVICES["canary"]["command"])
    assert "synthetic.tenant_isolation.remote_canary" in command
    assert (REPO_ROOT / "synthetic" / "tenant_isolation" / "remote_canary.py").is_file()
    assert "volumes" not in SERVICES["canary"]
    api_dockerfile = (REPO_ROOT / "infra" / "api" / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY synthetic ./synthetic" in api_dockerfile


def test_every_interpolated_variable_is_defaulted_or_present_in_the_example() -> None:
    required: set[str] = set()
    optional: set[str] = set()
    for match in _VARIABLE_REFERENCE.finditer(COMPOSE_TEXT):
        name = match.group("name")
        modifier = match.group("modifier") or ""
        (optional if modifier.startswith((":-", "-")) else required).add(name)
    missing = sorted(name for name in required if name not in ENV_EXAMPLE)
    assert missing == [], f"required by the compose file, absent from the example: {missing}"
    # The other direction: an example that documents a variable nothing reads
    # is documentation rot, and rot in a credential contract is how a rotation
    # misses a copy.
    documented_only = sorted(set(ENV_EXAMPLE) - required - optional)
    assert documented_only == []


def test_the_backup_job_forwards_every_rclone_key_the_runbook_tells_you_to_set() -> None:
    """The failure this prevents is silent until 04:00 and looks like a typo.

    ``.env.prod`` is an interpolation source, not a container environment, so a
    credential the operator adds because the runbook said to reaches rclone only
    if ``backup`` forwards it by name. Forward ``TYPE`` alone and the job
    resolves a b2 remote with no key, fails the copy, and the mistake reads as
    bad credentials rather than a missing line in the Compose file. The runbook
    recipes and this list are one contract; drift either way is the bug.
    """
    runbook = (REPO_ROOT / "docs" / "deployment-runbook.md").read_text(encoding="utf-8")
    documented = set(re.findall(r"\bRCLONE_CONFIG_REMOTE_[A-Z0-9_]+\b", runbook))
    assert documented, "the runbook stopped naming the rclone variables at all"
    forwarded = {name for name in _environment("backup") if name.startswith("RCLONE_CONFIG_")}
    assert sorted(documented - forwarded) == []


def test_the_example_file_ships_no_credential_from_the_public_tree() -> None:
    secretish = {
        name: value
        for name, value in ENV_EXAMPLE.items()
        if any(marker in name for marker in ("PASSWORD", "SECRET", "TOKEN"))
    }
    assert secretish, "the example defines no secrets at all, which cannot be right"
    for name, value in sorted(secretish.items()):
        assert value not in DEV_CREDENTIAL_LITERALS, name
        assert value.startswith("REPLACE_ME__"), name


def test_the_example_states_the_password_generation_rule() -> None:
    """Every DSN is f-string-concatenated with no URL encoding.

    A password containing '@' mis-parses the host and a '%' is percent-decoded,
    and pgBouncer's userlist cannot represent a quote or a space at all. The
    rule is not a style note.
    """
    text = ENV_EXAMPLE_PATH.read_text(encoding="utf-8")
    assert "secrets.token_urlsafe(48)" in text
    assert "openssl rand -base64" in text


def test_the_edge_takes_its_issuer_from_one_switch_and_defaults_to_the_local_ca() -> None:
    """`acme` on the box, `internal` on a laptop, and one file for both.

    The default matters as much as the switch: an unset EDGE_TLS that fell
    through to ACME would ask Let's Encrypt for a certificate for
    app.localtest.me every time somebody ran the rehearsal, and burn a rate
    limit against a name nobody can validate.
    """
    caddyfile = CADDYFILE_PATH.read_text(encoding="utf-8")
    assert caddyfile.count("tls {$EDGE_TLS_ISSUER:internal}") == 2
    # `local_certs` would pin the internal issuer globally and silently win
    # over the per-site ACME argument -- an edge that looks configured and
    # serves a certificate no browser trusts. (The comments say so; the
    # directives are what this asserts.)
    directives = [
        line.strip()
        for line in caddyfile.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert "local_certs" not in directives
    # The admin API has no authentication of its own and nothing drives this
    # edge remotely.
    assert "admin off" in caddyfile
    # Both hostnames come from the same two variables everything else reads.
    assert "{$PUBLIC_APP_HOST:app.localtest.me}" in caddyfile
    assert "{$PUBLIC_AUTH_HOST:auth.localtest.me}" in caddyfile
    # And the resolution from EDGE_TLS to that single argument happens in one
    # place: the edge's own entrypoint, which also publishes the matching trust
    # anchor -- Caddy's root locally, the system bundle under ACME.
    published = " ".join(str(part) for part in SERVICES["edge"]["command"])
    assert "EDGE_TLS_ISSUER=internal" in published
    assert 'EDGE_TLS_ISSUER="$$ACME_EMAIL"' in published
    assert "/etc/ssl/certs/ca-certificates.crt /edge-ca/root.crt" in published
    assert _environment("edge")["EDGE_TLS"] == "${EDGE_TLS:-internal}"


def test_the_edge_publishes_its_ca_root_where_unprivileged_containers_can_read_it() -> None:
    """Caddy keeps its CA under 0700 -- the signing key is in there.

    Every container that verifies a certificate the edge issued runs
    unprivileged, so the edge republishes the public root at 0644 into its own
    volume, and the healthcheck is that file rather than the listener: a job
    that starts before the root is readable cannot verify anything.
    """
    edge = SERVICES["edge"]
    published = " ".join(str(part) for part in edge["command"])
    assert "/edge-ca/root.crt" in published
    assert "chmod 0644 /edge-ca/root.crt" in published
    assert "/edge-ca/root.crt" in " ".join(edge["healthcheck"]["test"])
    # The CA signing key's volume is never shared with anything.
    for name, body in SERVICES.items():
        if name == "edge":
            continue
        mounts = [str(mount) for mount in body.get("volumes", [])]
        assert [mount for mount in mounts if mount.startswith("caddy_data")] == [], name
    for consumer in ("web", "release", "verify"):
        mounts = [str(mount) for mount in SERVICES[consumer].get("volumes", [])]
        assert "edge_ca:/etc/ssl/edge-ca:ro" in mounts, consumer


def test_the_makefile_exposes_the_rehearsal_as_named_targets() -> None:
    makefile = MAKEFILE_PATH.read_text(encoding="utf-8")
    phony = " ".join(
        line for line in makefile.splitlines() if line.startswith(".PHONY:") or " prod-" in line
    )
    for target in (
        "up-prod",
        "prod-seed",
        "prod-verify",
        "prod-load",
        "prod-rollback-rehearsal",
        "prod-reset",
        "prod-down",
    ):
        assert re.search(rf"^{re.escape(target)}:", makefile, re.MULTILINE), target
        assert target in phony, target


def test_every_image_is_pulled_from_the_registry_ci_publishes_to() -> None:
    """One repository, one tag variable, and no service exempt.

    A deploy is `docker compose pull` at IMAGE_TAG=<sha> and nothing else, so a
    service still carrying a locally-built tag would silently keep whatever
    happened to be on the box across every release -- and a rollback would not
    move it. The upstream base images are the exception on purpose: postgres,
    redis and caddy are pinned by their own tags and are not ours to publish.
    """
    upstream = {"postgres:16", "redis:7", "caddy:2-alpine"}
    ours = [str(body["image"]) for body in SERVICES.values() if body.get("image") not in upstream]
    assert ours, "no service names an image of ours, which cannot be right"
    for image in ours:
        assert image.startswith("${IMAGE_REPOSITORY:-ghcr.io/kudratsingh/movielens-recsys}/"), image
        assert image.endswith(":${IMAGE_TAG:-main}"), image
    names = {image.split("/")[-1].split(":")[0] for image in ours}
    assert names == {"api", "features", "web", "pgbouncer", "keycloak", "backup", "k6"}


def test_every_image_of_ours_can_still_be_built_from_this_checkout() -> None:
    """The box pulls; a laptop builds the same file's images from source.

    Keeping `build:` next to the registry reference is what makes one compose
    file serve both -- and it is also what would let the box quietly build an
    image a failed pull left missing, which is why `make prod-pull` asserts
    every image is present before anything starts.
    """
    upstream = {"postgres:16", "redis:7", "caddy:2-alpine"}
    for name, body in SERVICES.items():
        if body.get("image") in upstream:
            continue
        assert "build" in body, f"{name} names one of our images but cannot build it"
    makefile = MAKEFILE_PATH.read_text(encoding="utf-8")
    assert "docker image inspect" in makefile, "prod-pull no longer asserts the images arrived"


def test_the_load_job_runs_the_k6_the_rest_of_the_project_measures_with() -> None:
    """ADR 0010 makes this version the thing that stops measurements drifting.

    A Dockerfile cannot read a file to build its own FROM line, so the pin is
    duplicated in infra/k6/Dockerfile and the duplicate is asserted rather than
    trusted -- a canary running a different k6 than the gate would quietly undo
    what ADR 0010 pinned the version for. The assertion moved here when the
    deployment config that used to carry it was deleted.
    """
    pinned = (REPO_ROOT / "infra" / "ci" / "k6-version").read_text(encoding="utf-8").strip()
    dockerfile = (REPO_ROOT / "infra" / "k6" / "Dockerfile").read_text(encoding="utf-8")
    assert f"ARG K6_VERSION={pinned}" in dockerfile
    assert SERVICES["loadcheck"]["build"]["args"]["K6_VERSION"] == f"${{K6_VERSION:-{pinned}}}"
    assert ENV_EXAMPLE["K6_VERSION"] == pinned


def test_every_long_lived_service_carries_a_memory_ceiling() -> None:
    """4 GB, ten services, and one leak away from taking the box down.

    The limits are ceilings rather than reservations and they deliberately sum
    to more than the box has -- what each one buys is a blast radius: the
    kernel kills the service that ran away, `docker inspect` says OOMKilled,
    and the other nine keep serving. The release jobs are exempt: they run one
    at a time, they are the memory-hungriest things here, and a job killed
    halfway through a migration is a far worse outcome than one that swaps.
    """
    for name in sorted(LONG_LIVED_SERVICES):
        assert "mem_limit" in SERVICES[name], name
    for name in sorted(JOB_SERVICES):
        assert "mem_limit" not in SERVICES[name], name


def test_the_production_stack_cannot_be_confused_with_the_demo_stack() -> None:
    """Different project, different volumes, different image tags.

    The project name is pinned in the file rather than left to the directory
    name so a stray `docker compose -f docker-compose.prod.yml down -v` cannot
    take the demo stack's volumes with it.
    """
    assert COMPOSE["name"] == "movielens-prod"
    tags = [str(body["image"]) for body in SERVICES.values() if "image" in body]
    assert [tag for tag in tags if tag.endswith(":demo")] == []
