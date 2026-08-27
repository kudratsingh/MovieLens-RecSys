"""Structural gates on the box: its systemd units, its Docker daemon
configuration, its bootstrap script and the deploy script CI drives it with.

None of this is code that runs in CI, on a laptop, or anywhere a test could
exercise it end to end -- it runs once on a Hetzner VPS, as root, and the
cheapest way to find out it was wrong is an outage. So the properties that
would cost the most to get wrong are asserted from the files themselves:

* the stack comes back after a reboot, and stops without destroying anything;
* the nightly backup and the weekly prune fire when they claim to, and the
  prune cannot reach a volume;
* container logs rotate, because a full disk on a single box makes Postgres
  refuse writes and the first symptom is 500s;
* bootstrap.sh is re-runnable, and cannot lock the operator out of the machine
  it is hardening;
* deploy.sh's contract -- its usage, its sentinels, the release record the
  workflow reads back, and the Makefile targets it delegates to -- is the one
  the rest of the deployment was built against.

Nothing here starts a container, reads the network, or needs systemd.
"""

from __future__ import annotations

import configparser
import json
import re
import stat
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HOST_DIR = REPO_ROOT / "infra" / "host"
BOOTSTRAP_PATH = HOST_DIR / "bootstrap.sh"
DAEMON_JSON_PATH = HOST_DIR / "docker-daemon.json"
DEPLOY_PATH = REPO_ROOT / "infra" / "deploy" / "deploy.sh"
MAKEFILE_PATH = REPO_ROOT / "Makefile"

BOOTSTRAP = BOOTSTRAP_PATH.read_text(encoding="utf-8")
DEPLOY = DEPLOY_PATH.read_text(encoding="utf-8")
MAKEFILE = MAKEFILE_PATH.read_text(encoding="utf-8")

UNITS = (
    "movielens.service",
    "movielens-backup.service",
    "movielens-backup.timer",
    "movielens-prune.service",
    "movielens-prune.timer",
)

# Where the checkout, the secrets and the release record live. The units, the
# deploy script, the CI workflows and the runbook all name this path; it is a
# constant of the deployment rather than a variable.
APP_DIR = "/opt/movielens"

# The one compose invocation. The Makefile defines it, and the systemd unit has
# to spell the same thing out because systemd has no Makefile.
COMPOSE_INVOCATION = (
    "docker compose -p movielens-prod -f docker-compose.prod.yml --env-file .env.prod"
)


def _unit(name: str) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    # systemd directives are case-sensitive; configparser lowercases by default.
    parser.optionxform = str  # type: ignore[method-assign,assignment]
    parser.read_string((HOST_DIR / name).read_text(encoding="utf-8"))
    return parser


# --- the units --------------------------------------------------------------


@pytest.mark.parametrize("name", UNITS)
def test_every_unit_bootstrap_installs_is_in_the_repository(name: str) -> None:
    """The script installs a list; the list has to be the files that exist.

    A unit named in bootstrap.sh but absent from infra/host/ fails `install`
    halfway through a bootstrap run, leaving a box with some of its units.
    """
    assert (HOST_DIR / name).is_file()
    assert name in BOOTSTRAP, f"bootstrap.sh does not install {name}"


def test_bootstrap_installs_nothing_that_is_not_here() -> None:
    installed = set(re.findall(r"movielens[a-z-]*\.(?:service|timer)", BOOTSTRAP))
    assert installed == set(UNITS)


def test_the_stack_unit_brings_the_site_back_after_a_reboot() -> None:
    """The only reason this unit exists.

    Container restart policies survive a Docker daemon restart but not a `down`
    and not a reboot with no `up` behind it, and nothing else on the box would
    ever issue that `up`. WantedBy=multi-user.target is the whole mechanism.
    """
    unit = _unit("movielens.service")
    assert unit["Install"]["WantedBy"] == "multi-user.target"
    assert unit["Unit"]["Requires"] == "docker.service"
    assert "docker.service" in unit["Unit"]["After"]
    # oneshot + RemainAfterExit: `up -d` returns as soon as the containers are
    # created, so there is no process to supervise -- what is worth recording
    # is that the stack has been brought up.
    assert unit["Service"]["Type"] == "oneshot"
    assert unit["Service"]["RemainAfterExit"] == "yes"
    assert unit["Service"]["WorkingDirectory"] == APP_DIR
    assert unit["Service"]["User"] == "deploy"


def test_the_stack_unit_starts_the_stack_the_way_everything_else_does() -> None:
    """A boot and a deploy have to produce the same containers.

    Two spellings of the compose invocation is two projects, two env files or
    two sets of volumes waiting to happen -- and the failure would show up as a
    reboot quietly starting a second, empty stack.
    """
    exec_start = _unit("movielens.service")["Service"]["ExecStart"]
    assert exec_start.startswith("/usr/bin/"), "systemd requires an absolute ExecStart"
    assert COMPOSE_INVOCATION in exec_start
    assert "up -d" in exec_start
    # --wait, or the unit reports success while the site is still 502ing.
    assert "--wait" in exec_start
    assert COMPOSE_INVOCATION in MAKEFILE.replace("$(PROD_ENV_FILE)", ".env.prod")


def test_the_stack_unit_stops_rather_than_downs() -> None:
    """`down` removes containers and the network and buys nothing on a shutdown.

    It is also one flag away from `down --volumes`, which is the single most
    destructive command available on this box.
    """
    exec_stop = _unit("movielens.service")["Service"]["ExecStop"]
    assert exec_stop.endswith("stop")
    assert "down" not in exec_stop
    assert "-v" not in exec_stop.split()


def test_the_backup_runs_nightly_at_04_utc() -> None:
    """The timezone is stated, not inherited.

    A box moved to a local zone would otherwise shift the window silently, and
    every other timestamp in this system -- audit rows, the backup script's
    retention arithmetic, the k6 evidence -- is UTC.
    """
    timer = _unit("movielens-backup.timer")
    assert timer["Timer"]["OnCalendar"] == "*-*-* 04:00:00 UTC"
    # A box that was off at 04:00 still gets its backup rather than skipping a
    # night in silence.
    assert timer["Timer"]["Persistent"] == "true"
    assert timer["Install"]["WantedBy"] == "timers.target"


def test_the_backup_unit_runs_the_compose_job_and_not_a_second_implementation() -> None:
    """pg_dump, age and rclone live in the backup image, once.

    A unit that dumped the databases itself would be a second copy of the
    retention rules and the encryption step, and the copy nobody tests.
    """
    exec_start = _unit("movielens-backup.service")["Service"]["ExecStart"]
    assert COMPOSE_INVOCATION in exec_start
    assert exec_start.endswith("run --rm -T backup")


def test_the_prune_is_weekly_and_cannot_reach_a_volume() -> None:
    """The command that would destroy both databases is one word away.

    `docker volume prune` -- or `system prune --volumes` -- would take both
    Postgres data directories and the Redis AOF the moment a deploy had the
    containers stopped. Images are the only thing this box reclaims
    automatically, and a week's worth are kept so a rollback has local layers
    to reuse.
    """
    timer = _unit("movielens-prune.timer")
    assert timer["Timer"]["OnCalendar"].startswith("Sun ")
    assert timer["Install"]["WantedBy"] == "timers.target"

    exec_start = _unit("movielens-prune.service")["Service"]["ExecStart"]
    assert "docker image prune" in exec_start
    assert "--filter until=168h" in exec_start
    assert "--force" in exec_start
    for name in UNITS:
        text = (HOST_DIR / name).read_text(encoding="utf-8")
        directives = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
        body = "\n".join(directives)
        assert "volume prune" not in body, name
        assert "system prune" not in body, name
        assert "--volumes" not in body, name


@pytest.mark.parametrize("name", UNITS)
def test_no_unit_carries_a_credential(name: str) -> None:
    """Everything secret arrives through .env.prod, by path.

    A unit file is world-readable and lands in /etc/systemd/system; a value
    typed into one would be a credential in a place nobody thinks to rotate.
    """
    text = (HOST_DIR / name).read_text(encoding="utf-8")
    for marker in ("PASSWORD", "SECRET", "TOKEN", "-e ", "--env "):
        assert marker not in text, f"{name} carries {marker!r}"


# --- the Docker daemon ------------------------------------------------------


def test_container_logs_rotate() -> None:
    """40 GB, no log shipper, and json-file's default is no rotation at all.

    One chatty container fills the disk, Postgres starts refusing writes, and
    the audit insert every recommendation depends on fails closed -- so the
    first symptom of a full disk is 500s rather than a disk alert.
    """
    config = json.loads(DAEMON_JSON_PATH.read_text(encoding="utf-8"))
    assert config["log-driver"] == "json-file"
    assert config["log-opts"]["max-size"] == "20m"
    assert config["log-opts"]["max-file"] == "5"


def test_bootstrap_installs_the_daemon_config_from_this_file() -> None:
    """One definition, copied -- not a heredoc that drifts from it.

    The comparison before the copy is what makes the step idempotent: the
    daemon is restarted only when the file actually changed, because restarting
    Docker on a box that is serving is not a no-op.
    """
    assert "docker-daemon.json" in BOOTSTRAP
    assert "/etc/docker/daemon.json" in BOOTSTRAP
    assert 'cmp -s "$HOST_DIR/docker-daemon.json" /etc/docker/daemon.json' in BOOTSTRAP


# --- bootstrap.sh -----------------------------------------------------------


def test_bootstrap_is_re_runnable() -> None:
    """It is the only written record of how the box is configured.

    A record that cannot be replayed after an edit is a record that drifts from
    the machine, and then the only way to reproduce the box is to remember what
    was done by hand. Every step is guarded; these are the guards.
    """
    guards = (
        'id -u "$DEPLOY_USER"',  # the account
        'grep -qxF "$ssh_key" "$authorized_keys"',  # the key, appended once
        "cmp -s",  # the daemon config, restarted only on change
        "swapon --show",  # the swapfile
        "grep -qxF '/swapfile none swap sw 0 0' /etc/fstab",
        'id -nG "$DEPLOY_USER"',  # the docker group
    )
    for guard in guards:
        assert guard in BOOTSTRAP, guard


def test_bootstrap_cannot_lock_the_operator_out() -> None:
    """Disabling password authentication is irreversible from the outside.

    The order is the safety property: refuse before hardening if nothing can
    authenticate with a key, then assert the *effective* sshd configuration
    rather than trusting that the drop-in was read.
    """
    refusal = BOOTSTRAP.index('[ -s "$authorized_keys" ] || die')
    hardening = BOOTSTRAP.index("PasswordAuthentication no")
    assert refusal < hardening, "the lockout guard must run before the hardening"
    assert "sshd -t ||" in BOOTSTRAP, "the configuration is not validated before the reload"
    assert 'sshd -T | awk \'$1 == "passwordauthentication"' in BOOTSTRAP


def test_bootstrap_wins_the_sshd_drop_in_ordering() -> None:
    """First value wins, and Hetzner's images ship a 50-cloud-init.conf.

    Ubuntu includes /etc/ssh/sshd_config.d/*.conf at the top of sshd_config and
    sshd takes the first value it sees for a keyword, so a 99- file parses,
    applies nothing, and leaves password logins open behind cloud-init's
    `PasswordAuthentication yes` while looking closed.
    """
    assert "/etc/ssh/sshd_config.d/01-movielens.conf" in BOOTSTRAP
    assert "/etc/ssh/sshd_config.d/99" not in BOOTSTRAP


def test_bootstrap_opens_exactly_three_ports() -> None:
    """SSH, HTTP, HTTPS. Everything else in the topology is private.

    HTTP is open because ACME's http-01 challenge and the edge's redirect to
    https both need it, not because anything is served there.
    """
    assert "for port in 22 80 443; do" in BOOTSTRAP
    assert 'ufw allow "$port/tcp"' in BOOTSTRAP
    assert "ufw --force default deny incoming" in BOOTSTRAP
    assert "ufw --force enable" in BOOTSTRAP
    # And the caveat is written down rather than assumed away: Docker publishes
    # through its own iptables chain, which ufw does not filter.
    assert "DOCKER-USER" in BOOTSTRAP


def test_bootstrap_installs_docker_from_dockers_own_repository() -> None:
    """Ubuntu's docker.io has no compose v2 plugin.

    Every compose invocation in this repository is `docker compose`, not the v1
    script, so the apt source is load-bearing rather than a preference.
    """
    assert "download.docker.com/linux/ubuntu" in BOOTSTRAP
    assert "/etc/apt/keyrings/docker.asc" in BOOTSTRAP
    for package in ("docker-ce", "docker-compose-plugin", "docker-buildx-plugin"):
        assert package in BOOTSTRAP, package


def test_bootstrap_leaves_the_box_patching_itself() -> None:
    """One box, one owner, and nobody watching a CVE feed."""
    assert "unattended-upgrades" in BOOTSTRAP
    assert 'APT::Periodic::Unattended-Upgrade "1";' in BOOTSTRAP


def test_bootstrap_never_writes_a_secret() -> None:
    """It hardens the box; .env.prod is filled in by hand afterwards.

    A bootstrap that generated credentials would put them in the shell history
    of the machine it was hardening, and in this script's own output.
    """
    # It does fix the mode when the file is already there, which is the only
    # thing it has to do with secrets.
    assert 'chmod 0600 "$APP_DIR/.env.prod"' in BOOTSTRAP
    # But it never generates one, and never writes into the file.
    assert "token_urlsafe" not in BOOTSTRAP
    assert "openssl rand" not in BOOTSTRAP
    assert ">$APP_DIR/.env.prod" not in BOOTSTRAP.replace(" ", "")
    assert "set -x" not in BOOTSTRAP


# --- deploy.sh --------------------------------------------------------------


@pytest.mark.parametrize("path", [BOOTSTRAP_PATH, DEPLOY_PATH])
def test_every_host_script_is_strict_and_executable(path: Path) -> None:
    """set -euo pipefail, and a mode git will carry to the box.

    Without the executable bit the systemd unit and the CI workflow both fail
    on a file they can read perfectly well.
    """
    text = path.read_text(encoding="utf-8")
    assert text.startswith("#!/usr/bin/env bash\n")
    assert "set -euo pipefail" in text
    assert stat.S_IMODE(path.stat().st_mode) & 0o111, f"{path.name} is not executable"


def test_the_deploy_script_states_both_of_its_forms() -> None:
    assert "usage: deploy.sh <sha>" in DEPLOY
    assert "deploy.sh --rollback" in DEPLOY


def test_the_deploy_script_prints_the_sentinels_ci_reads() -> None:
    """The exit code alone cannot say which release is serving.

    An automatic rollback exits non-zero -- the release did not ship -- and the
    sentinel is the only thing that distinguishes "rolled back cleanly, site is
    up" from "the box needs a human".
    """
    assert "printf 'DEPLOY-OK %s\\n'" in DEPLOY
    assert "printf 'ROLLBACK-OK %s\\n'" in DEPLOY
    assert "printf 'ROLLBACK-FAILED %s\\n'" in DEPLOY
    assert "printf 'DEPLOY-FAILED %s\\n'" in DEPLOY


def test_the_deploy_script_refuses_without_the_secrets_file() -> None:
    """Every credential in the stack is generated; there is no default.

    Failing on the missing file beats failing four services later on an
    interpolation error, with half the release applied.
    """
    assert '[ -f "$ENV_FILE" ] || die' in DEPLOY
    assert 'ENV_FILE="$REPO_ROOT/.env.prod"' in DEPLOY


def test_the_deploy_script_records_the_release_where_the_workflow_reads_it() -> None:
    """.release/current and .release/previous are a contract, not an internal.

    The deploy workflow uploads them as its rollback-target artifact, and
    `deploy.sh --rollback` is only as good as what is in them.
    """
    assert 'RELEASE_DIR="$REPO_ROOT/.release"' in DEPLOY
    assert 'CURRENT_FILE="$RELEASE_DIR/current"' in DEPLOY
    assert 'PREVIOUS_FILE="$RELEASE_DIR/previous"' in DEPLOY


def test_the_deploy_script_delegates_to_makefile_targets_that_exist() -> None:
    """The Makefile owns the sequence; a second copy here would drift from it.

    This is the assertion that catches a renamed target, which would otherwise
    surface as a failed deploy at 02:00 with `make: *** No rule to make target`.
    """
    targets = sorted(set(re.findall(r'"\$MAKE" (prod-[a-z-]+)', DEPLOY)))
    assert targets == ["prod-pull", "prod-release", "prod-serve", "prod-verify"]
    for target in targets:
        assert re.search(rf"^{re.escape(target)}:", MAKEFILE, re.MULTILINE), target
        assert target in MAKEFILE.split("\n.PHONY:")[1].split("\n")[0], f"{target} is not .PHONY"


def test_every_release_step_stops_the_release_when_it_fails() -> None:
    """`set -e` does not reach inside an `if` condition, and this is one.

    ``deploy_release`` is only ever called as ``if deploy_release ...``, and
    bash suspends errexit for everything evaluated in a condition -- the body
    of a called function included. Without an explicit ``|| return`` on each
    step, a failed pull or a failed migration is stepped over, the function
    reports whatever its *last* command returned, and a release nobody
    completed ends in ``DEPLOY-OK`` at exit 0. That was the observed behaviour
    before this assertion existed, and it is invisible in a dry run because a
    dry run's steps all succeed.
    """
    body = DEPLOY.split("deploy_release() {", 1)[1].split("\n}", 1)[0]
    steps = re.findall(r'run "\$MAKE" (prod-[a-z-]+)(.*)', body)
    assert len(steps) == 4, f"expected the four release steps, found {steps}"
    for target, tail in steps:
        assert "|| return" in tail, f"{target} cannot fail the release"


def test_the_makefile_exposes_the_deploy_and_rollback_entry_points() -> None:
    """`make prod-deploy IMAGE_TAG=<sha>` and `make prod-rollback`.

    The environment-clearing wrapper is asserted too: a variable set on make's
    command line is exported to sub-makes as an override and beats an
    environment variable set inside the recipe, so without it a rollback
    started by `make prod-deploy IMAGE_TAG=<sha>` would pull the release it was
    rolling back from.
    """
    for target in ("prod-deploy", "prod-rollback"):
        assert re.search(rf"^{re.escape(target)}:", MAKEFILE, re.MULTILINE), target
    assert MAKEFILE.count("env -u IMAGE_TAG MAKEFLAGS= bash infra/deploy/deploy.sh") == 2


def test_the_deploy_script_never_prints_the_environment_file() -> None:
    """Its whole output goes into a CI log.

    Compose reads .env.prod by path; nothing here has any reason to read it,
    and `set -x` would echo every variable the release steps expand.
    """
    assert "set -x" not in DEPLOY
    assert 'cat "$ENV_FILE"' not in DEPLOY
    assert "grep" not in DEPLOY.replace("# ", "")
