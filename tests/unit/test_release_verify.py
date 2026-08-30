"""What the post-deploy verifier refuses to call a pass.

Everything here is about one property, from two directions. A verification job
that goes green because it could not run a check is worse than no job at all —
it converts an outage into a green tick — so:

* the cross-tenant row (non-negotiable #9) runs the deployed-stack canary for
  real, and the absence of the second actor it needs is a *failed row*, not a
  quiet omission;
* a selected row that this build ran nothing for is reported as failed, so
  ``VERIFY-OK`` means every row ran and held rather than every row that
  happened to run.

Nothing here reaches a network: the canary is stubbed at the module boundary,
which is the seam that matters, because what is being asserted is how ``verify``
reacts to each of its three answers (passed, found something, could not run).
"""

from __future__ import annotations

from typing import Any

import pytest

from src.config import Settings
from src.release import VERIFY_SENTINEL
from src.release import verify as verify_module
from src.release.verify import (
    CHECK_IDS,
    CHECK_ORDER,
    CHECKS,
    CheckResult,
    VerifyConfig,
    VerifyRun,
    check_tenant_isolation,
    run_checks,
)
from synthetic.tenant_isolation.remote_canary import UnreachableTargetError


def _config(**overrides: Any) -> VerifyConfig:
    values: dict[str, Any] = {
        "api_url": "http://api.test",
        "web_url": "http://web.test",
        "keycloak_url": "http://keycloak.test",
        "keycloak_public_base_url": "https://auth.test",
        "realm": "demo",
        "client_id": "movielens-verify",
        "client_secret": "verify-client-secret",
        "username": "verify",
        "password": "verify-password",
        "audience": "movielens-api",
        "app_origin": "https://app.test",
        "admin_realm": "master",
        "admin_client_id": "",
        "admin_client_secret": "",
        "admin_username": "recsys-admin",
        "admin_password": "admin-password",
        "isolation_realm": "default",
        "isolation_username": "isolation",
        "isolation_password": "isolation-password",
        "service_client_id": "movielens-api",
        "warm_user_id": 900000101,
        "write_user_id": 900000103,
        "audit_window_hours": 24,
        "timeout": 5.0,
    }
    values.update(overrides)
    return VerifyConfig(**values)


def _run(**overrides: Any) -> VerifyRun:
    settings = Settings(_env_file=None, environment="dev")
    # The client is never used: every test below stubs the canary itself.
    return VerifyRun(_config(**overrides), settings, client=None)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# V-6 is a row this build runs, not a row it documents
# --------------------------------------------------------------------------


def test_the_isolation_row_is_registered_everywhere_a_row_has_to_be() -> None:
    """Three tables, and a row missing from any of them runs nothing.

    ``CHECK_IDS`` decides what ``--all`` selects, ``CHECKS`` holds the callable
    and ``CHECK_ORDER`` decides what actually executes. The dispatch guard turns
    the third omission into a failure rather than a silent pass; this keeps the
    three in step in the first place."""
    assert "V-6" in CHECK_IDS
    assert CHECKS["V-6"][1] is check_tenant_isolation
    assert set(CHECK_ORDER) == set(CHECK_IDS)


def test_the_isolation_row_passes_on_a_clean_canary_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def fake_canary(client: Any, **kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs)
        return {
            "target": kwargs["api_url"],
            "tenant_a": kwargs["actor_a"].tenant_id,
            "tenant_b": kwargs["actor_b"].tenant_id,
            "routes_probed": 20,
            "sentinel_persona_ids": [900000101, 900000102],
            "failures": [],
            "passed": True,
        }

    monkeypatch.setattr(verify_module, "run_isolation_canary", fake_canary)
    result = check_tenant_isolation(_run())

    assert result.passed is True
    assert result.evidence["routes_probed"] == 20
    # The row says what made the absences meaningful, not just that they held.
    assert result.evidence["sentinel_persona_ids"] == [900000101, 900000102]
    assert "sentinel personas" in result.detail
    # The two actors are the point of the row: one from a realm that must be
    # refused, one from the realm the deployment serves.
    assert (seen["actor_a"].realm, seen["actor_a"].username) == ("default", "isolation")
    assert (seen["actor_b"].realm, seen["actor_b"].username) == ("demo", "verify")
    # And the actor that must be refused is checked against the client the API
    # trusts by azp alone, read from this deployment's own setting.
    assert seen["service_client_id"] == "movielens-api"


def test_the_isolation_row_fails_when_the_canary_finds_a_leak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        verify_module,
        "run_isolation_canary",
        lambda client, **kwargs: {
            "tenant_a": "default",
            "tenant_b": "demo",
            "routes_probed": 20,
            "failures": [
                {
                    "route": "GET /users/900000101/history?limit=1",
                    "status": 200,
                    "detail": "answered a foreign tenant's persona route with a success status",
                }
            ],
            "passed": False,
        },
    )
    with pytest.raises(verify_module.CheckFailedError) as error:
        check_tenant_isolation(_run())
    assert "GET /users/900000101/history" in str(error.value)


def test_a_canary_that_could_not_run_is_a_failed_row_and_not_a_skip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The unreachable-target case, which is where a skip would be tempting."""

    def unreachable(client: Any, **kwargs: Any) -> dict[str, Any]:
        raise UnreachableTargetError("Keycloak is unreachable at http://keycloak.test")

    monkeypatch.setattr(verify_module, "run_isolation_canary", unreachable)
    with pytest.raises(verify_module.CheckFailedError) as error:
        check_tenant_isolation(_run())
    assert "failure and not a skip" in str(error.value)


def test_the_isolation_row_fails_when_the_job_holds_no_second_actor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No ISOLATION_PASSWORD means nothing was proven, and it has to say so.

    This is the shape the deployment gets wrong first — a variable missing from
    a panel — and the one where reporting a skip would leave ``VERIFY-OK``
    standing over an unproven isolation boundary."""

    def never_called(client: Any, **kwargs: Any) -> dict[str, Any]:
        raise AssertionError("the canary must not run without an actor to run it as")

    monkeypatch.setattr(verify_module, "run_isolation_canary", never_called)
    results = run_checks(_run(isolation_password=""), ["V-6"])

    assert [(result.id, result.passed) for result in results] == [("V-6", False)]
    assert "ISOLATION_PASSWORD" in results[0].detail


# --------------------------------------------------------------------------
# A row that did not run has proven nothing
# --------------------------------------------------------------------------


def test_a_selected_row_this_build_never_ran_is_reported_as_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The dispatch guard.

    A check registered in ``CHECKS`` but absent from ``CHECK_ORDER`` would
    otherwise be silently dropped from the report, and ``--all`` would still
    print the sentinel — the deploy gate greps for that sentinel."""
    monkeypatch.setattr(verify_module, "CHECK_ORDER", ("V-0",))
    monkeypatch.setattr(verify_module, "CHECKS", {"V-0": ("readiness", lambda run: _passed("V-0"))})

    results = run_checks(_run(), ["V-0", "V-6"])

    assert [(result.id, result.passed) for result in results] == [("V-0", True), ("V-6", False)]
    assert "proven nothing" in results[1].detail


def _passed(check_id: str) -> CheckResult:
    return CheckResult(check_id, "stub", True, "stubbed")


def test_the_command_every_deployment_runs_parses(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--all`` is the whole matrix and takes no positional rows.

    It exited 2 with "invalid choice: []" until the positional stopped
    declaring `choices`: argparse validates a ``nargs="*"`` argument's default
    against them, so the verify job, its cron and the deploy gate's sentinel
    grep were all downstream of a command that could not start."""
    assert verify_module.build_parser().parse_args(["--all"]).checks == []
    assert verify_module.build_parser().parse_args(["V-6", "V-9"]).checks == ["V-6", "V-9"]

    with pytest.raises(SystemExit) as exit_status:
        verify_module.main(["V-99"])
    assert exit_status.value.code == 2
    assert "unknown matrix row" in capsys.readouterr().err


def test_the_sentinel_is_never_printed_over_a_failed_row(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(verify_module, "Settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(
        verify_module,
        "run_checks",
        lambda run, selected: [CheckResult("V-6", "tenant isolation", False, "could not run")],
    )

    assert verify_module.main(["--all"]) == 1
    captured = capsys.readouterr()
    assert VERIFY_SENTINEL not in captured.out
    assert "FAIL V-6" in captured.err
