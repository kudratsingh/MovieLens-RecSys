"""Structural properties of the two production deployment workflows.

Nothing in this repository executes ``.github/workflows/deploy-production.yml``
or ``production-canary.yml`` — GitHub does, against a Railway project that does
not exist yet — so the properties whose violation is invisible until a release
goes wrong are the ones worth pinning here.

Three of them are couplings across files that no single file can protect:

* the required-check list against the jobs that ``ci.yml`` actually declares, so
  a renamed, deleted or newly added CI job cannot leave the deploy gate quietly
  asserting nothing;
* the sentinel strings against ``src/release``, which is where the jobs that
  print them define the literals;
* the two plan-named gates ``api-contract-check`` and ``web-api-types-check``,
  which are *steps* rather than jobs. Requiring the ``lint`` and ``frontend``
  jobs is what requiring those two checks means, and that only stays true while
  the steps are still in those jobs.

The sentinel match is exercised with the real ``grep`` rather than a
reimplementation of its pattern, because the property that matters —
``VERIFY-OK`` must not be satisfied by a run that printed ``VERIFY-SUBSET-OK``
— is a property of that regular expression under that tool.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from src.release import RELEASE_BOOTSTRAP_SENTINEL, VERIFY_SENTINEL, VERIFY_SUBSET_SENTINEL

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"
DEPLOY_WORKFLOW = WORKFLOW_DIR / "deploy-production.yml"
CANARY_WORKFLOW = WORKFLOW_DIR / "production-canary.yml"
CI_WORKFLOW = WORKFLOW_DIR / "ci.yml"

# The private half of the topology. None of these may ever carry a domain or a
# TCP proxy: the feature server has no authentication at all, the model
# sidecar's /healthz is unauthenticated, and the data stores answer to whoever
# holds a password.
PRIVATE_SERVICES = (
    "api",
    "model-server",
    "feature-server",
    "pgbouncer",
    "redis",
    "postgres-app",
    "postgres-keycloak",
)

# The order the release sequence has to keep. Keycloak first because the API
# cannot become ready until its serving realm exists; the release job next
# because the API cannot become ready until the migrations have run; the model
# sidecar before the API because the API's recommendations depend on it and its
# own pre-deploy materialises the features it refuses to boot without.
DEPLOY_SEQUENCE = (
    "keycloak",
    "release",
    "model-server",
    "feature-server",
    "api",
    "web",
    "verify",
)

# Reverse order over the services a failed release may have promoted. Keycloak
# and the release job are deliberately absent: reverting the identity provider
# is an identity migration, and re-running an older bootstrap against a newer
# database turns one incident into two.
ROLLBACK_SEQUENCE = ("web", "api", "feature-server", "model-server")


def load_workflow(path: Path) -> dict[str, Any]:
    document = yaml.safe_load(path.read_text())
    assert isinstance(document, dict)
    return document


def triggers(workflow: dict[str, Any]) -> dict[str, Any]:
    # PyYAML resolves the bare key `on` to the boolean True under YAML 1.1,
    # which is the one place a workflow file and a YAML parser disagree.
    return workflow.get("on", workflow.get(True))  # type: ignore[call-overload]


def step_names(workflow: dict[str, Any], job: str) -> list[str]:
    return [step.get("name", step.get("uses", "")) for step in workflow["jobs"][job]["steps"]]


def step_by_name(workflow: dict[str, Any], job: str, name: str) -> dict[str, Any]:
    for step in workflow["jobs"][job]["steps"]:
        if step.get("name") == name:
            return step
    raise AssertionError(f"{job} has no step named {name!r}")


def helper_source(workflow: dict[str, Any], job: str) -> str:
    """The shell helper each workflow writes into $RUNNER_TEMP and later sources."""
    run = step_by_name(workflow, job, "Prepare Railway API access")["run"]
    match = re.search(r"<<'HELPER'\n(.*?)\n *HELPER\n", run, re.DOTALL)
    assert match is not None, f"{job} no longer writes a HELPER heredoc"
    return match.group(1)


@pytest.fixture(scope="module")
def deploy() -> dict[str, Any]:
    return load_workflow(DEPLOY_WORKFLOW)


@pytest.fixture(scope="module")
def canary() -> dict[str, Any]:
    return load_workflow(CANARY_WORKFLOW)


@pytest.fixture(scope="module")
def ci() -> dict[str, Any]:
    return load_workflow(CI_WORKFLOW)


def test_deploy_runs_on_dispatch_and_pushes_to_main(deploy: dict[str, Any]) -> None:
    on = triggers(deploy)
    assert "workflow_dispatch" in on
    assert on["push"]["branches"] == ["main"]


def test_the_ci_gate_runs_before_the_reviewer_is_asked(deploy: dict[str, Any]) -> None:
    # The `production` environment carries a required reviewer. Proving CI green
    # in a job that does not declare the environment means the approval prompt
    # only ever appears for a commit that has already passed.
    assert "environment" not in deploy["jobs"]["gate"]
    assert deploy["jobs"]["deploy"]["environment"] == "production"
    assert deploy["jobs"]["deploy"]["needs"] == "gate"


def test_the_gate_names_every_ci_job(deploy: dict[str, Any], ci: dict[str, Any]) -> None:
    gated = set(deploy["env"]["REQUIRED_CHECKS"].split())
    gated |= set(deploy["env"]["CONDITIONAL_CHECKS"].split())
    declared = set(ci["jobs"])
    assert gated == declared, (
        "the deploy gate and ci.yml have drifted: "
        f"missing from the gate {sorted(declared - gated)}, "
        f"named by the gate but not declared by ci.yml {sorted(gated - declared)}"
    )


def test_only_the_conditional_ci_jobs_may_be_skipped(
    deploy: dict[str, Any], ci: dict[str, Any]
) -> None:
    # A `skipped` conclusion is accepted for exactly the jobs ci.yml gates on a
    # path filter, and for no others: everywhere else a skip is what a deleted or
    # mis-conditioned job looks like, and treating that as a pass is the whole
    # failure this gate exists to prevent.
    conditional = set(deploy["env"]["CONDITIONAL_CHECKS"].split())
    filtered = {name for name, job in ci["jobs"].items() if "if" in job}
    assert conditional == filtered, (
        "ci.yml's conditional jobs and the gate's skippable list have drifted: "
        f"conditional in ci.yml but not skippable {sorted(filtered - conditional)}, "
        f"skippable but unconditional in ci.yml {sorted(conditional - filtered)}"
    )
    assert not (conditional & set(deploy["env"]["REQUIRED_CHECKS"].split()))


def test_the_two_step_level_gates_are_still_inside_the_jobs_the_gate_requires(
    deploy: dict[str, Any], ci: dict[str, Any]
) -> None:
    # `api-contract-check` and `web-api-types-check` are named as required gates
    # by the deployment plan but exist as steps, not jobs. Requiring `lint` and
    # `frontend` is equivalent only while these two commands live there.
    required = set(deploy["env"]["REQUIRED_CHECKS"].split())
    assert {"lint", "frontend"} <= required

    lint_commands = " ".join(step.get("run", "") for step in ci["jobs"]["lint"]["steps"])
    assert "scripts.generate_openapi --check" in lint_commands

    frontend_commands = " ".join(step.get("run", "") for step in ci["jobs"]["frontend"]["steps"])
    assert "api:types:check" in frontend_commands


def test_the_gate_refuses_a_run_that_is_not_completed_and_successful(
    deploy: dict[str, Any],
) -> None:
    script = step_by_name(deploy, "gate", "Resolve and check the CI run for this commit")["run"]
    # Waiting for an in-flight run is deliberate — a push starts CI and this
    # workflow at the same moment — but only `completed` plus `success` may pass,
    # and each required check is then confirmed in its own right, because a
    # skipped job does not fail its run.
    assert '"$status" = "completed"' in script
    assert '"$conclusion" != "success"' in script
    assert '"$result" != "success"' in script
    assert "Refusing to deploy" in script


def test_the_rollback_target_is_recorded_before_anything_is_deployed(
    deploy: dict[str, Any],
) -> None:
    names = step_names(deploy, "deploy")
    recorded = names.index("Record the current deployments as the rollback target")
    first_deploy = min(names.index(f"Deploy {service}") for service in ("keycloak", "api", "web"))
    assert recorded < first_deploy


def test_no_public_sidecar_covers_every_private_service(deploy: dict[str, Any]) -> None:
    script = step_by_name(
        deploy, "deploy", "Assert no sidecar or data store is publicly reachable"
    )["run"]
    for service in PRIVATE_SERVICES:
        assert service in script
    # Both an attached domain and an enabled TCP proxy make a private service
    # reachable, and only one of them is a "domain".
    assert "DOMAINS_QUERY" in script
    assert "TCP_PROXIES_QUERY" in script
    assert "refusing to deploy" in script


def test_no_public_sidecar_runs_before_the_first_deploy(deploy: dict[str, Any]) -> None:
    names = step_names(deploy, "deploy")
    assertion = names.index("Assert no sidecar or data store is publicly reachable")
    first_deploy = min(names.index(f"Deploy {service}") for service in ("keycloak", "api", "web"))
    assert assertion < first_deploy


def test_the_services_are_deployed_in_the_order_the_topology_requires(
    deploy: dict[str, Any],
) -> None:
    steps = deploy["jobs"]["deploy"]["steps"]
    # Anchored on the mechanism rather than on a step title: a step deploys a
    # service exactly when it calls start_deployment for it.
    # The failure path deploys `verify` a second time after rolling back, which
    # is not part of the release sequence.
    deployed = []
    for step in steps:
        if "if" in step:
            continue
        match = re.search(r"start_deployment (\S+) ", step.get("run", ""))
        if match:
            deployed.append(match.group(1))
    assert deployed == list(DEPLOY_SEQUENCE), f"deploy order drifted: {deployed}"


def test_the_two_job_services_are_gated_on_their_sentinels(deploy: dict[str, Any]) -> None:
    release = step_by_name(deploy, "deploy", "Run the release job")["run"]
    verify = step_by_name(deploy, "deploy", "Verify the deployment")["run"]
    assert "wait_for_sentinel release " in release
    assert RELEASE_BOOTSTRAP_SENTINEL in release
    assert "wait_for_sentinel verify " in verify
    assert VERIFY_SENTINEL in verify
    # A partial matrix prints VERIFY-SUBSET-OK, and a canary or a release that
    # accepted it would be reporting on checks it never ran.
    assert VERIFY_SUBSET_SENTINEL not in verify


def test_the_rollback_reverts_only_the_services_it_can_safely_revert(
    deploy: dict[str, Any],
) -> None:
    step = step_by_name(deploy, "deploy", "Roll back to the recorded deployments")
    assert step["if"] == "failure()"
    assert f"for name in {' '.join(ROLLBACK_SEQUENCE)}; do" in step["run"]
    # Reverting Keycloak is an identity migration and re-running an older
    # bootstrap against a newer database is how one incident becomes two.
    assert "keycloak" not in step["run"].split("for name in")[1].split(";")[0]
    # The recorded ids are the target, and verify runs again afterwards to say
    # whether the rollback restored a working deployment.
    assert "rollback_deployment_id" in step["run"]
    assert VERIFY_SENTINEL in step["run"]


def test_the_rollback_target_is_published_as_an_artifact(deploy: dict[str, Any]) -> None:
    step = step_by_name(deploy, "deploy", "Upload the rollback target")
    assert step["uses"].startswith("actions/upload-artifact@")
    # Uploaded even when the release fails: that is the run whose rollback
    # target somebody actually needs.
    assert step["if"] == "always()"


def test_the_canary_runs_every_thirty_minutes_and_on_demand(canary: dict[str, Any]) -> None:
    on = triggers(canary)
    assert on["schedule"] == [{"cron": "*/30 * * * *"}]
    assert "workflow_dispatch" in on


def test_the_canary_shares_the_deploy_workflow_s_concurrency_lane(
    deploy: dict[str, Any], canary: dict[str, Any]
) -> None:
    # A canary that redeploys `verify` in the middle of a release reads the wrong
    # deployment's log and reports on a system that is mid-change.
    assert canary["concurrency"]["group"] == deploy["concurrency"]["group"]
    assert canary["concurrency"]["cancel-in-progress"] is False
    assert deploy["concurrency"]["cancel-in-progress"] is False


def test_the_canary_never_ships_code(canary: dict[str, Any]) -> None:
    body = CANARY_WORKFLOW.read_text()
    # serviceInstanceRedeploy reuses the service's existing commit;
    # serviceInstanceDeployV2 would deploy a new one, which is a release.
    assert "serviceInstanceRedeploy" in body
    assert "serviceInstanceDeployV2" not in body
    assert "actions/checkout" not in body


def test_the_canary_s_verdict_comes_from_verify_and_not_from_the_loadcheck(
    canary: dict[str, Any],
) -> None:
    verify = step_by_name(canary, "canary", "Run the verification matrix")["run"]
    loadcheck = step_by_name(canary, "canary", "Run the advisory loadcheck")["run"]
    assert VERIFY_SENTINEL in verify
    assert "exit 1" in verify
    # V-10 records latency with no verdict, so the loadcheck reports and warns
    # but never decides the run.
    assert "exit 1" not in loadcheck
    assert "::warning::" in loadcheck


def test_neither_workflow_carries_a_credential(
    deploy: dict[str, Any], canary: dict[str, Any]
) -> None:
    for workflow, job in ((deploy, "deploy"), (canary, "canary")):
        env = workflow["jobs"][job]["env"]
        assert env["RAILWAY_TOKEN"] == "${{ secrets.RAILWAY_TOKEN }}"
        assert env["RAILWAY_API_TOKEN"] == "${{ secrets.RAILWAY_API_TOKEN }}"
        # Ids are configuration, not secrets, and are read from environment
        # variables so a wrong one is visible in the log.
        assert env["RAILWAY_PROJECT_ID"] == "${{ vars.RAILWAY_PROJECT_ID }}"
        assert env["RAILWAY_ENVIRONMENT_ID"] == "${{ vars.RAILWAY_ENVIRONMENT_ID }}"

    for path in (DEPLOY_WORKFLOW, CANARY_WORKFLOW):
        body = path.read_text()
        for reference in re.findall(r"\$\{\{\s*secrets\.([A-Za-z0-9_]+)\s*\}\}", body):
            assert reference in {"RAILWAY_TOKEN", "RAILWAY_API_TOKEN"}


@pytest.mark.parametrize("job,path", [("deploy", DEPLOY_WORKFLOW), ("canary", CANARY_WORKFLOW)])
def test_the_embedded_shell_helper_parses(job: str, path: Path) -> None:
    bash = shutil.which("bash")
    if bash is None:  # pragma: no cover - bash is present on every CI runner
        pytest.skip("bash is not available")
    source = helper_source(load_workflow(path), job)
    result = subprocess.run([bash, "-n"], input=source, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_the_sentinel_match_rejects_a_partial_verify_run(deploy: dict[str, Any]) -> None:
    grep = shutil.which("grep")
    if grep is None:  # pragma: no cover - grep is present on every CI runner
        pytest.skip("grep is not available")

    source = helper_source(deploy, "deploy")
    match = re.search(r'grep -Eq "(?P<pattern>[^"]+)"', source)
    assert match is not None, "wait_for_sentinel no longer greps for the sentinel"
    template = match.group("pattern")

    def matches(sentinel: str, line: str) -> bool:
        pattern = template.replace("${sentinel}", sentinel)
        return (
            subprocess.run(
                [grep, "-Eq", pattern], input=line, capture_output=True, text=True, check=False
            ).returncode
            == 0
        )

    # The release job prints its sentinel with the applied revision after it.
    assert matches(RELEASE_BOOTSTRAP_SENTINEL, f"{RELEASE_BOOTSTRAP_SENTINEL} 0012")
    assert matches(VERIFY_SENTINEL, VERIFY_SENTINEL)
    # The one that matters: a run that skipped rows of the matrix prints
    # VERIFY-SUBSET-OK, and must not be read as a full pass.
    assert not matches(VERIFY_SENTINEL, VERIFY_SUBSET_SENTINEL)
    assert not matches(VERIFY_SENTINEL, "not-VERIFY-OK-either")
