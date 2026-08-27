"""Structural properties of the Railway service configs under ``infra/railway/``.

These files are never executed by anything in this repository — the platform
reads them — so nothing else in the tree would notice a typo in a key name, a
``dockerfilePath`` pointing at a file that has been moved, or a service that
quietly lost its config file. That is what this module is for: the properties
below are the ones whose violation is invisible until a deploy goes wrong.

The one non-obvious assertion is the last: ``infra/k6/Dockerfile`` has to
duplicate the k6 version pinned in ``infra/ci/k6-version``, because a Dockerfile
cannot read a file to build its own ``FROM`` line. ADR 0010 pins that version so
local and CI measurements cannot drift apart; a post-deploy canary running a
different k6 would undo it silently.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
RAILWAY_DIR = REPO_ROOT / "infra" / "railway"

# Exactly the twelve services of the deployment topology that Railway builds or
# runs from this repository. The two Postgres services come from Railway's own
# template and deliberately have no file here.
EXPECTED_SERVICES = {
    "api",
    "backup",
    "feature-server",
    "keycloak",
    "keycloak-provision",
    "loadcheck",
    "model-server",
    "pgbouncer",
    "redis",
    "release",
    "verify",
    "web",
}

# Only keys the platform documents. An unrecognised key is not rejected by
# Railway with an error — it is ignored — so a misspelling presents as a setting
# that silently never applied.
ALLOWED_TOP_LEVEL = {"$schema", "build", "deploy"}
ALLOWED_BUILD = {"builder", "dockerfilePath", "watchPatterns", "buildCommand"}
ALLOWED_DEPLOY = {
    "startCommand",
    "preDeployCommand",
    "healthcheckPath",
    "healthcheckTimeout",
    "restartPolicyType",
    "restartPolicyMaxRetries",
    "cronSchedule",
    "overlapSeconds",
    "drainingSeconds",
    "multiRegionConfig",
}
RESTART_POLICIES = {"ON_FAILURE", "ALWAYS", "NEVER"}

# The health paths are spelled differently on purpose and none of the
# differences has a fallback: Feast's server serves /health, the API's readiness
# probe is /readyz, and Keycloak is probed on the master realm because the
# serving realm does not exist on the first deploy.
EXPECTED_HEALTHCHECKS = {
    "api": "/readyz",
    "model-server": "/healthz",
    "feature-server": "/health",
    "web": "/",
    "keycloak": "/realms/master/.well-known/openid-configuration",
}
# Neither speaks HTTP, and Railway has no other kind of check.
NO_HEALTHCHECK = {"pgbouncer", "redis"}
# Jobs: a job that restarted would re-run a migration, a provisioning script or
# a backup, so NEVER is a correctness setting rather than a preference.
JOBS = {"release", "keycloak-provision", "verify", "backup", "loadcheck"}
EXPECTED_CRONS = {"verify": "17 6 * * *", "backup": "0 4 * * *"}


def _load(service: str) -> dict[str, Any]:
    config: Any = json.loads((RAILWAY_DIR / f"{service}.json").read_text(encoding="utf-8"))
    assert isinstance(config, dict), f"{service}.json must be a JSON object"
    return config


SERVICE_CONFIGS = sorted(EXPECTED_SERVICES)


def test_every_topology_service_has_exactly_one_config_file() -> None:
    on_disk = {path.stem for path in RAILWAY_DIR.glob("*.json")}
    assert on_disk == EXPECTED_SERVICES


@pytest.mark.parametrize("service", SERVICE_CONFIGS)
def test_config_parses_and_uses_only_supported_keys(service: str) -> None:
    config = _load(service)
    assert set(config) <= ALLOWED_TOP_LEVEL, f"{service}: unsupported top-level key"
    assert config["$schema"] == "https://railway.com/railway.schema.json"

    build = config.get("build", {})
    assert set(build) <= ALLOWED_BUILD, f"{service}: unsupported build key"
    deploy = config.get("deploy", {})
    assert set(deploy) <= ALLOWED_DEPLOY, f"{service}: unsupported deploy key"
    assert deploy, f"{service}: a config with no deploy block decides nothing"


@pytest.mark.parametrize("service", SERVICE_CONFIGS)
def test_dockerfile_paths_resolve(service: str) -> None:
    build = _load(service).get("build")
    if build is None:
        # redis runs a public image; there is nothing for Railway to build.
        assert service == "redis"
        return

    assert build["builder"] == "DOCKERFILE"
    dockerfile_path = build["dockerfilePath"]
    # `web` is the one service with a Root Directory, so its dockerfilePath is
    # relative to `web/` while this config file's own path stays
    # repo-root-relative.
    base = REPO_ROOT / "web" if service == "web" else REPO_ROOT
    assert (base / dockerfile_path).is_file(), f"{service}: {dockerfile_path} does not exist"


@pytest.mark.parametrize("service", SERVICE_CONFIGS)
def test_restart_policy_matches_what_the_service_is(service: str) -> None:
    deploy = _load(service)["deploy"]
    policy = deploy["restartPolicyType"]
    assert policy in RESTART_POLICIES
    if service in JOBS:
        assert policy == "NEVER", f"{service} is a job; restarting it would re-run its work"
    if "restartPolicyMaxRetries" in deploy:
        assert policy == "ON_FAILURE", f"{service}: a retry count only applies to ON_FAILURE"


@pytest.mark.parametrize("service", SERVICE_CONFIGS)
def test_healthcheck_paths_are_the_ones_the_images_actually_serve(service: str) -> None:
    deploy = _load(service)["deploy"]
    if service in EXPECTED_HEALTHCHECKS:
        assert deploy["healthcheckPath"] == EXPECTED_HEALTHCHECKS[service]
        # Railway's default is already 300 s, but the model-server's probe waits
        # on a real warm-up rather than on a socket, so the value belongs in the
        # file where it can be read rather than inferred.
        assert deploy["healthcheckTimeout"] == 300
    else:
        assert "healthcheckPath" not in deploy, f"{service} serves no HTTP health path"
        assert service in NO_HEALTHCHECK or service in JOBS


def test_cron_schedules_are_declared_only_on_the_two_scheduled_jobs() -> None:
    for service in SERVICE_CONFIGS:
        deploy = _load(service)["deploy"]
        assert deploy.get("cronSchedule") == EXPECTED_CRONS.get(service), service


def test_the_shared_api_image_is_built_from_one_pattern_set() -> None:
    # api, release and verify are three services on one image. Divergent watch
    # patterns would mean three different answers to "does this commit change
    # that image".
    patterns = {
        service: _load(service)["build"]["watchPatterns"]
        for service in ("api", "release", "verify")
    }
    assert patterns["api"] == patterns["release"] == patterns["verify"]
    assert "infra/api/**" in patterns["api"]
    # The image copies these too, so a change to either does change the image.
    assert "alembic/**" in patterns["api"]
    assert "synthetic/**" in patterns["api"]


def test_the_sidecars_watch_the_baked_serving_bundle() -> None:
    for service in ("model-server", "feature-server"):
        patterns = _load(service)["build"]["watchPatterns"]
        # The bundle is baked into the image, so a new bundle is a new image.
        assert "infra/model-bundle/**" in patterns, service


def test_loadcheck_runs_a_script_the_image_actually_carries() -> None:
    config = _load("loadcheck")
    assert config["build"]["dockerfilePath"] == "infra/k6/Dockerfile"
    start_command = config["deploy"]["startCommand"]
    script = start_command.rsplit(" ", 1)[-1]
    assert script.startswith("/scripts/")
    # infra/k6/Dockerfile copies synthetic/load to /scripts wholesale.
    assert (REPO_ROOT / "synthetic" / "load" / script[len("/scripts/") :]).is_file()


def test_k6_image_pin_matches_the_repository_wide_k6_version() -> None:
    pinned = (REPO_ROOT / "infra" / "ci" / "k6-version").read_text(encoding="utf-8").strip()
    dockerfile = (REPO_ROOT / "infra" / "k6" / "Dockerfile").read_text(encoding="utf-8")
    default = re.search(r"^ARG K6_VERSION=(\S+)$", dockerfile, flags=re.MULTILINE)
    assert default is not None, "infra/k6/Dockerfile must pin k6 through ARG K6_VERSION"
    assert default.group(1) == pinned, (
        "infra/k6/Dockerfile and infra/ci/k6-version disagree; the canary would run a "
        "different k6 than the pinned gate"
    )
    assert "FROM grafana/k6:${K6_VERSION}" in dockerfile
