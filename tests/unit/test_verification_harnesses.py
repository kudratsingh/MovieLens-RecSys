"""The verification harnesses have to travel, and they have to fail loudly.

Three properties are asserted here, one per harness:

* the deployed-stack tenant-isolation canary probes *every* persona-guarded
  route the application actually declares, and treats an unreachable target as
  a failure rather than a skip;
* the tenant-isolation conftest still skips a missing local stack, but fails
  when the caller declared one was mandatory;
* the k6 canary profile carries its own thresholds, and the pinned SLO gate the
  smoke and nightly profiles run under is untouched.
"""

from __future__ import annotations

import base64
import inspect
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.routing import APIRoute

from synthetic.smoke.demo import DEFAULT_AUTH, AuthConfig, DemoSmokeError, service_access_token
from synthetic.tenant_isolation.remote_canary import (
    PERSONA_ROUTES,
    REQUIRE_STACK_ENV,
    TENANT_A_USER_ID,
    TENANT_B_PERSONA_ID,
    TENANT_B_PERSONA_IDS,
    UNROUTABLE_MOVIE_ID,
    UNROUTABLE_USER_ID,
    Actor,
    ActorMisconfiguredError,
    UnreachableTargetError,
    assert_denied_by_design,
    check_actors_resolve_to_different_tenants,
    check_guard_denies_foreign_actor,
    check_no_foreign_tenant_in_payload,
    check_sentinel_is_visible_to_its_owner,
    live_stack_required,
    main,
    mint_token,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
LOAD_DIR = REPO_ROOT / "synthetic" / "load"


# --------------------------------------------------------------------------
# The remote canary's route table
# --------------------------------------------------------------------------


def _guarded_routes() -> set[tuple[str, str]]:
    """Every (method, path) behind the demo-persona guard, read from the app.

    Handlers either call `_require_demo_persona_access` directly or route
    through `_feedback_mutation`, which calls it first thing. Reading the source
    rather than a hand-kept list is the point: a new guarded route that nobody
    adds to the canary shows up here as a failing test.
    """
    from src.serving.app import app

    guarded: set[tuple[str, str]] = set()
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        source = inspect.getsource(route.endpoint)
        if "_require_demo_persona_access(" not in source and "_feedback_mutation(" not in source:
            continue
        for method in (route.methods or set()) - {"HEAD", "OPTIONS"}:
            guarded.add((method, route.path))
    return guarded


def test_canary_probes_every_persona_guarded_route() -> None:
    declared = {(route.method, route.template) for route in PERSONA_ROUTES}
    assert declared == _guarded_routes()


def test_canary_addresses_mutations_so_a_broken_guard_still_fails_closed() -> None:
    for route in PERSONA_ROUTES:
        if route.method == "GET":
            continue
        user_id = UNROUTABLE_USER_ID if route.unscoped_mutation else TENANT_B_PERSONA_ID
        path = route.path(user_id=user_id, movie_id=UNROUTABLE_MOVIE_ID)
        # Either the write names a movie that exists in no catalog, or -- for
        # the bulk rating reset, which names no movie at all -- a user id that
        # is nobody's persona. Both fail closed on a 404 if the guard is gone.
        assert str(UNROUTABLE_MOVIE_ID) in path or str(UNROUTABLE_USER_ID) in path

    # The routes that name no movie, so the unowned user id is the only thing
    # standing between a broken guard and a real persona's state.
    bulk = [route for route in PERSONA_ROUTES if route.unscoped_mutation]
    assert [route.template for route in bulk] == [
        "/users/{user_id}/ratings",
        "/users/{user_id}/preferences",
    ]


def test_route_paths_resolve_ids_and_keep_query_strings() -> None:
    by_template = {route.template: route for route in PERSONA_ROUTES}
    library = by_template["/users/{user_id}/library"]
    assert library.path(user_id=7, movie_id=9) == "/users/7/library?tab=rated&limit=1"
    detail = by_template["/users/{user_id}/movies/{movie_id}"]
    assert detail.path(user_id=7, movie_id=9) == "/users/7/movies/9"


# --------------------------------------------------------------------------
# What the canary calls a pass
# --------------------------------------------------------------------------


def _client(handler: Any) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://api.test")


def test_guard_check_passes_only_on_403() -> None:
    with _client(lambda request: httpx.Response(403, json={"detail": "nope"})) as client:
        findings = check_guard_denies_foreign_actor(client, api_url="http://api.test", token="t")
    assert len(findings) == len(PERSONA_ROUTES)
    assert all(finding.passed for finding in findings)


def test_guard_check_reports_404_as_the_wrong_denial() -> None:
    with _client(lambda request: httpx.Response(404, json={"detail": "unknown"})) as client:
        findings = check_guard_denies_foreign_actor(client, api_url="http://api.test", token="t")
    assert not any(finding.passed for finding in findings)
    assert "demo-impersonator" in findings[0].detail


def test_guard_check_fails_when_a_foreign_persona_route_answers_200() -> None:
    with _client(lambda request: httpx.Response(200, json={"items": []})) as client:
        findings = check_guard_denies_foreign_actor(client, api_url="http://api.test", token="t")
    assert not any(finding.passed for finding in findings)
    assert findings[0].status == 200


def test_payload_check_finds_a_foreign_tenant_nested_in_a_list() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "tenant_id": "demo",
                "items": [{"movie_id": 1, "audit": {"tenant_id": "default"}}],
            },
        )

    with _client(handler) as client:
        findings = check_no_foreign_tenant_in_payload(
            client,
            api_url="http://api.test",
            token="t",
            tenant_id="demo",
            user_id=TENANT_A_USER_ID,
        )
    assert not any(finding.passed for finding in findings)
    assert "['default']" in findings[0].detail


def test_payload_check_accepts_a_denial_but_not_a_server_error() -> None:
    with _client(lambda request: httpx.Response(404, json={"detail": "unknown"})) as client:
        denied = check_no_foreign_tenant_in_payload(
            client, api_url="http://api.test", token="t", tenant_id="demo"
        )
    assert all(finding.passed for finding in denied)

    with _client(lambda request: httpx.Response(503, text="upstream down")) as client:
        errored = check_no_foreign_tenant_in_payload(
            client, api_url="http://api.test", token="t", tenant_id="demo"
        )
    assert not any(finding.passed for finding in errored)
    assert errored[0].detail == "the target neither answered nor denied"


# --------------------------------------------------------------------------
# The controls: two tenants, one of which has rows worth hiding
#
# Every finding above is an absence, and an absence over an empty tenant is not
# isolation (issue #75). These two checks are what stop the deployed canary
# reporting a pass for a boundary nothing tried to cross.
# --------------------------------------------------------------------------


def _persona_page(*user_ids: int, tenant_id: str = "demo") -> dict[str, Any]:
    return {
        "tenant_id": tenant_id,
        "items": [
            {"user_id": user_id, "slug": f"p{user_id}", "display_name": "P", "description": ""}
            for user_id in user_ids
        ],
    }


def test_the_positive_control_passes_when_tenant_b_holds_its_sentinel_personas() -> None:
    page = _persona_page(*TENANT_B_PERSONA_IDS)
    with _client(lambda request: httpx.Response(200, json=page)) as client:
        findings = check_sentinel_is_visible_to_its_owner(
            client, api_url="http://api.test", token="t", tenant_id="demo"
        )
    assert [finding.passed for finding in findings] == [True]
    assert "do exist" in findings[0].detail


def test_the_positive_control_fails_when_there_is_nothing_to_leak() -> None:
    """The exact shape of the vacuity: tenant B answers, and answers empty."""
    with _client(lambda request: httpx.Response(200, json=_persona_page())) as client:
        findings = check_sentinel_is_visible_to_its_owner(
            client, api_url="http://api.test", token="t", tenant_id="demo"
        )
    assert not any(finding.passed for finding in findings)
    assert "consistent with there being nothing to leak" in findings[0].detail


def test_the_positive_control_fails_when_tenant_b_cannot_read_its_own_personas() -> None:
    with _client(lambda request: httpx.Response(403, json={"detail": "nope"})) as client:
        findings = check_sentinel_is_visible_to_its_owner(
            client, api_url="http://api.test", token="t", tenant_id="demo"
        )
    assert not any(finding.passed for finding in findings)
    assert findings[0].status == 403


def test_the_positive_control_still_reports_a_leak_inside_its_own_page() -> None:
    """The control reads a payload, so it checks the payload it read."""
    page = _persona_page(*TENANT_B_PERSONA_IDS)
    page["items"][0]["tenant_id"] = "default"
    with _client(lambda request: httpx.Response(200, json=page)) as client:
        findings = check_sentinel_is_visible_to_its_owner(
            client, api_url="http://api.test", token="t", tenant_id="demo"
        )
    assert not any(finding.passed for finding in findings)
    assert "['default']" in findings[0].detail


def _whoami_client(by_token: dict[str, httpx.Response]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        token = request.headers["Authorization"].removeprefix("Bearer ")
        return by_token[token]

    return _client(handler)


_ACTOR_A = Actor("default", "movielens-verify", "s", "isolation", "pw")
_ACTOR_B = Actor("demo", "movielens-verify", "s", "verify", "pw")


def test_the_actor_control_passes_when_the_two_realms_resolve_to_two_tenants() -> None:
    with _whoami_client(
        {
            "a": httpx.Response(200, json={"tenant_id": "default", "realm": "default"}),
            "b": httpx.Response(200, json={"tenant_id": "demo", "realm": "demo"}),
        }
    ) as client:
        findings = check_actors_resolve_to_different_tenants(
            client,
            api_url="http://api.test",
            token_a="a",
            token_b="b",
            actor_a=_ACTOR_A,
            actor_b=_ACTOR_B,
        )
    assert all(finding.passed for finding in findings)


def test_the_actor_control_fails_when_both_actors_land_in_one_tenant() -> None:
    """A run comparing a tenant with itself would report a wall of 200s as a leak."""
    same = httpx.Response(200, json={"tenant_id": "demo"})
    with _whoami_client({"a": same, "b": same}) as client:
        findings = check_actors_resolve_to_different_tenants(
            client,
            api_url="http://api.test",
            token_a="a",
            token_b="b",
            actor_a=Actor("demo", "movielens-verify", "s", "isolation", "pw"),
            actor_b=_ACTOR_B,
        )
    assert not all(finding.passed for finding in findings)
    assert any("compared a tenant with itself" in finding.detail for finding in findings)


def test_the_actor_control_fails_when_an_actor_is_not_an_authenticated_caller() -> None:
    with _whoami_client(
        {
            "a": httpx.Response(401, text="invalid token"),
            "b": httpx.Response(200, json={"tenant_id": "demo"}),
        }
    ) as client:
        findings = check_actors_resolve_to_different_tenants(
            client,
            api_url="http://api.test",
            token_a="a",
            token_b="b",
            actor_a=_ACTOR_A,
            actor_b=_ACTOR_B,
        )
    assert [finding.passed for finding in findings] == [False, True]
    assert "not an authenticated caller" in findings[0].detail


def test_the_actor_control_fails_when_tenant_a_is_told_about_tenant_bs_personas() -> None:
    with _whoami_client(
        {
            "a": httpx.Response(
                200, json={"tenant_id": "default", "selected_persona": TENANT_B_PERSONA_ID}
            ),
            "b": httpx.Response(200, json={"tenant_id": "demo"}),
        }
    ) as client:
        findings = check_actors_resolve_to_different_tenants(
            client,
            api_url="http://api.test",
            token_a="a",
            token_b="b",
            actor_a=_ACTOR_A,
            actor_b=_ACTOR_B,
        )
    assert [finding.passed for finding in findings] == [False, True]
    assert str(TENANT_B_PERSONA_ID) in findings[0].detail


# --------------------------------------------------------------------------
# An unreachable target is a failure, never a skip
# --------------------------------------------------------------------------


def _fake_access_token(**claims: Any) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"header.{payload}.signature"


def test_isolation_actor_must_not_be_the_trusted_service_client() -> None:
    actor = Actor("default", "movielens-api", "secret", "isolation", "pw")
    token = _fake_access_token(azp="movielens-api", realm_access={"roles": ["user"]})
    with pytest.raises(ActorMisconfiguredError, match="trusted service client"):
        assert_denied_by_design(token, actor=actor, service_client_id="movielens-api")


def test_isolation_actor_must_not_hold_the_persona_role() -> None:
    actor = Actor("default", "movielens-verify", "secret", "isolation", "pw")
    token = _fake_access_token(
        azp="movielens-verify", realm_access={"roles": ["user", "demo-impersonator"]}
    )
    with pytest.raises(ActorMisconfiguredError, match="demo-impersonator"):
        assert_denied_by_design(token, actor=actor, service_client_id="movielens-api")


def test_an_actor_denied_by_design_passes_the_preflight() -> None:
    actor = Actor("default", "movielens-verify", "secret", "isolation", "pw")
    token = _fake_access_token(azp="movielens-verify", realm_access={"roles": ["user"]})
    assert_denied_by_design(token, actor=actor, service_client_id="movielens-api")


def test_an_unreadable_token_stops_the_run_rather_than_being_assumed_safe() -> None:
    with pytest.raises(ActorMisconfiguredError, match="not a JWS"):
        assert_denied_by_design(
            "opaque-token",
            actor=Actor("default", "movielens-verify", "", "isolation", "pw"),
            service_client_id="movielens-api",
        )


def test_mint_token_raises_when_keycloak_refuses() -> None:
    with _client(lambda request: httpx.Response(401, text="invalid_grant")) as client:
        with pytest.raises(UnreachableTargetError, match="refused the 'default' token request"):
            mint_token(
                client,
                keycloak_url="http://keycloak.test",
                actor=Actor("default", "movielens-api", "", "isolation", "wrong"),
            )


def test_main_exits_non_zero_and_says_nothing_was_proven(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Port 1 is reserved and never listening, so this is an offline, immediate
    # connection refusal -- the same shape as a deployment that is down.
    exit_code = main(
        [
            "--api-url",
            "http://127.0.0.1:1",
            "--keycloak-url",
            "http://127.0.0.1:1",
            "--timeout",
            "2",
        ]
    )
    assert exit_code == 2
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["passed"] is False
    assert report["unreachable"] is True
    assert "UNREACHABLE TARGET" in captured.err
    assert "failure, not a skip" in captured.err


def test_live_stack_required_reads_the_shared_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(REQUIRE_STACK_ENV, raising=False)
    assert live_stack_required() is False
    for truthy in ("1", "true", "YES", " on "):
        monkeypatch.setenv(REQUIRE_STACK_ENV, truthy)
        assert live_stack_required() is True
    for falsy in ("0", "false", ""):
        monkeypatch.setenv(REQUIRE_STACK_ENV, falsy)
        assert live_stack_required() is False


# --------------------------------------------------------------------------
# The conftest gate: skip locally, fail when a stack was demanded
# --------------------------------------------------------------------------


def _collect_tenant_isolation(**env_overrides: str) -> subprocess.CompletedProcess[str]:
    """Collect the tenant-isolation suite with the local stack made unreachable.

    The probe is pointed nowhere by giving the subprocess a proxy on a dead
    port: httpx honours the standard proxy variables, so this reproduces "the
    stack is not up" on a machine where it happens to be. Collection stops at
    the conftest either way, so nothing here touches a database.
    """
    env = os.environ.copy()
    env.pop(REQUIRE_STACK_ENV, None)
    env.update(
        {
            "HTTP_PROXY": "http://127.0.0.1:1",
            "HTTPS_PROXY": "http://127.0.0.1:1",
            "ALL_PROXY": "http://127.0.0.1:1",
            "NO_PROXY": "",
        }
    )
    env.update(env_overrides)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/tenant_isolation",
            "--collect-only",
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


def test_missing_stack_still_skips_for_a_local_run() -> None:
    result = _collect_tenant_isolation()
    output = result.stdout + result.stderr
    # The outcome asserted is the *kind* of outcome, not the exit code: pytest
    # renders a module-level skip in a directory's own conftest as "1 skipped"
    # when it reaches that directory during collection, and as an unhandled
    # Skipped when the directory is the invocation argument. Both are skips,
    # both predate this switch, and neither claims the canaries ran.
    assert "Skipped" in output or "skipped" in output, output
    assert "make infra-up" in output, output
    assert REQUIRE_STACK_ENV not in output
    assert "never executed" not in output


def test_missing_stack_fails_when_the_caller_demanded_one() -> None:
    result = _collect_tenant_isolation(**{REQUIRE_STACK_ENV: "1"})
    output = result.stdout + result.stderr
    assert result.returncode not in {0, 5}, output
    assert REQUIRE_STACK_ENV in output
    assert "never executed" in output


# --------------------------------------------------------------------------
# The parameterised demo smoke
# --------------------------------------------------------------------------


def test_demo_smoke_defaults_reproduce_the_previously_hardcoded_identity() -> None:
    assert (DEFAULT_AUTH.realm, DEFAULT_AUTH.client_id, DEFAULT_AUTH.grant_type) == (
        "demo",
        "movielens-api",
        "client_credentials",
    )
    assert DEFAULT_AUTH.token_form() == {
        "grant_type": "client_credentials",
        "client_id": "movielens-api",
        "client_secret": "movielens-api-secret-dev-only",
    }


def test_demo_smoke_password_grant_targets_the_named_realm_and_user() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["form"] = dict(httpx.QueryParams(request.content.decode()))
        return httpx.Response(200, json={"access_token": "token-value"})

    auth = AuthConfig(
        realm="production",
        client_id="movielens-verify",
        client_secret="",
        grant_type="password",
        username="verify",
        password="s3cret",
    )
    with _client(handler) as client:
        token = service_access_token(client, "http://auth.example.com", auth)

    assert token == "token-value"
    assert seen["url"] == (
        "http://auth.example.com/realms/production/protocol/openid-connect/token"
    )
    assert seen["form"] == {
        "grant_type": "password",
        "client_id": "movielens-verify",
        "username": "verify",
        "password": "s3cret",
    }


def test_demo_smoke_password_grant_needs_a_user() -> None:
    with pytest.raises(DemoSmokeError, match="--username and --password"):
        AuthConfig(grant_type="password")


def test_demo_smoke_rejects_an_unsupported_grant() -> None:
    with pytest.raises(DemoSmokeError, match="unsupported grant type"):
        AuthConfig(grant_type="authorization_code")


# --------------------------------------------------------------------------
# The k6 profiles and their thresholds
# --------------------------------------------------------------------------


def _threshold_entries(path: Path) -> dict[str, list[str]]:
    """Parse a k6 thresholds module into {metric: [expressions]}."""
    source = path.read_text()
    entries = re.findall(r'"([^"]+)":\s*\[([^\]]*)\]', source)
    return {metric: re.findall(r'"([^"]*)"', values) for metric, values in entries}


def test_pinned_slo_thresholds_are_exactly_what_they_have_always_been() -> None:
    # Non-negotiables #4 and #11. If this test fails, someone moved the gate:
    # that needs a re-measured baseline and an ADR 0010 entry, not a green diff.
    assert _threshold_entries(LOAD_DIR / "thresholds.js") == {
        "checks{endpoint:recommendations}": ["rate==1"],
        "http_req_duration{endpoint:recommendations}": ["p(99)<100"],
        "http_req_failed{endpoint:recommendations}": ["rate==0"],
        "http_reqs{endpoint:recommendations}": ["rate>50"],
    }


def test_canary_thresholds_assert_correctness_and_claim_no_slo() -> None:
    canary = LOAD_DIR / "canary_thresholds.js"
    assert _threshold_entries(canary) == {
        "checks{endpoint:recommendations}": ["rate==1"],
        "http_req_failed{endpoint:recommendations}": ["rate==0"],
        # Not a gate. k6 materialises a tagged sub-metric only if a threshold
        # names it, and handleSummary reads p50/p95/p99 off precisely this one,
        # so without this line the canary's summary reports null latency -- the
        # opposite of its remit, which is to record p99 and not judge it.
        # A duration is never negative, so this can never fail.
        "http_req_duration{endpoint:recommendations}": ["p(99)>=0"],
    }
    # The omission is the point: a second place that appears to define the p99
    # SLO is how a pinned threshold quietly drifts. So what is asserted here is
    # the property rather than the spelling -- no latency bound that could
    # *fail*, and no achieved-throughput floor.
    exported = canary.read_text().split("export const", 1)[1]
    assert "p(99)<" not in exported
    assert "http_reqs" not in exported


def test_only_the_prod_canary_profile_swaps_the_thresholds() -> None:
    source = (LOAD_DIR / "recommendations.js").read_text()
    assert 'PROFILE === "prod-canary" ? canaryThresholds : recommendationThresholds' in source
    assert "thresholds: selectedThresholds," in source


def test_load_profiles_keep_the_measured_workload_and_add_the_canary() -> None:
    source = (LOAD_DIR / "recommendations.js").read_text()

    def profile(name: str) -> dict[str, str]:
        body = re.search(rf'\n  "?{re.escape(name)}"?: \{{(.*?)\n  \}},', source, re.DOTALL)
        assert body is not None, f"profile {name} is missing from recommendations.js"
        return dict(re.findall(r"(\w+): \"?([\w.]+)\"?,", body.group(1)))

    # The two measured profiles are the accepted baseline's workload; nothing
    # in a deployment bundle may reshape them.
    assert profile("smoke") == {
        "duration": "60s",
        "preAllocatedVUs": "10",
        "maxVUs": "40",
        "rate": "55",
    }
    assert profile("nightly") == {
        "duration": "5m",
        "preAllocatedVUs": "100",
        "maxVUs": "400",
        "rate": "600",
    }
    assert profile("prod-canary") == {
        "duration": "60s",
        "preAllocatedVUs": "5",
        "maxVUs": "20",
        "rate": "5",
    }


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed on this host")
@pytest.mark.parametrize("script", ["recommendations.js", "thresholds.js", "canary_thresholds.js"])
def test_load_scripts_parse_as_modules(script: str) -> None:
    """k6 only reports a syntax error once a stack is up and a run has started.

    Node parses the same ES module grammar, so this catches the typo minutes
    earlier than the load job would, at the cost of one subprocess.
    """
    result = subprocess.run(
        ["node", "--input-type=module", "--check"],
        input=(LOAD_DIR / script).read_text(),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
