"""How a refused mutation reaches the wire, on every route that can refuse one.

The state machine itself is covered in ``test_serving_feedback.py``. What is
under test here is the boundary above it: which status each refusal answers
with, and whether the body carries the machine-readable ``code`` a client is
supposed to branch on. Both used to be one undifferentiated ``409`` whose only
distinguishing feature was the sentence in ``detail``, which the web client
matched with regular expressions (issue #74).

The routes are the real ones — lifted off the application and mounted on a bare
app — so a route that forgets to catch a refusal fails here rather than in a
browser. Only the middleware stack is left behind: auth, RLS and the audit
writer each need a live dependency, and none of them is what decides a status.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from src.serving.app import CodedHTTPException, _coded_http_exception, app
from src.serving.feedback import (
    FeedbackMutationError,
    IdempotencyConflictError,
    InvalidStateTransitionError,
    StateRevisionConflictError,
)

USER = 900000101
MOVIE = 101

# Every route that runs a movie-state mutation, including the two
# compatibility shapes that predate `user_movie_state` and had never caught a
# refusal at all — they answered 500.
MUTATIONS: list[tuple[str, str, dict[str, Any] | None]] = [
    ("PUT", f"/users/{USER}/movies/{MOVIE}/watched", None),
    ("DELETE", f"/users/{USER}/movies/{MOVIE}/watched", None),
    ("PUT", f"/users/{USER}/movies/{MOVIE}/rating", {"rating": 4.0}),
    ("DELETE", f"/users/{USER}/movies/{MOVIE}/rating", None),
    ("PUT", f"/users/{USER}/movies/{MOVIE}/watchlist", None),
    ("DELETE", f"/users/{USER}/movies/{MOVIE}/watchlist", None),
    ("PUT", f"/users/{USER}/movies/{MOVIE}/dismissal", None),
    ("DELETE", f"/users/{USER}/movies/{MOVIE}/dismissal", None),
    ("PUT", f"/users/{USER}/ratings/{MOVIE}", {"rating": 4.0}),
    ("DELETE", f"/users/{USER}/ratings", None),
]

# The templates the same routes are declared under, which is what selects them
# off the application. Asserted against the parametrized paths below so the two
# lists cannot drift apart into a suite that silently tests nothing.
MUTATION_TEMPLATES = {
    "/users/{user_id}/movies/{movie_id}/watched",
    "/users/{user_id}/movies/{movie_id}/rating",
    "/users/{user_id}/movies/{movie_id}/watchlist",
    "/users/{user_id}/movies/{movie_id}/dismissal",
    "/users/{user_id}/ratings/{movie_id}",
    "/users/{user_id}/ratings",
}


class _Row:
    def __init__(self, movie_id: int) -> None:
        self.movie_id = movie_id


class _Connection:
    """Enough of a connection for the bulk clear to find a row to clear."""

    def execute(self, statement: Any, parameters: Any = None) -> list[_Row]:
        return [_Row(MOVIE)]


class _RefusingFeedback:
    """A ``FeedbackService`` that only ever turns the mutation away."""

    def __init__(self, error: FeedbackMutationError) -> None:
        self._error = error

    def require_persona(self, connection: Any, *, user_id: int) -> None:
        return None

    def mutate(self, connection: Any, **kwargs: Any) -> None:
        raise self._error


def _probe_app() -> FastAPI:
    probe = FastAPI()
    probe.add_exception_handler(CodedHTTPException, _coded_http_exception)
    probe.router.routes.extend(
        route
        for route in app.routes
        if isinstance(route, APIRoute) and route.path in MUTATION_TEMPLATES
    )

    @probe.middleware("http")
    async def bind_request_state(request: Any, call_next: Any) -> Any:
        # What AuthMiddleware would have attached. The persona check reads the
        # principal, and every handler reads the RLS-bound connection off it.
        request.state.principal = SimpleNamespace(
            tenant_id="demo",
            user_id="oidc-actor",
            can_access_demo_personas=lambda **_: True,
        )
        request.state.db = _Connection()
        return await call_next(request)

    return probe


def _call(error: FeedbackMutationError, method: str, path: str, body: dict[str, Any] | None):
    from src.serving import app as app_module

    original = app_module._feedback
    app_module._feedback = _RefusingFeedback(error)  # type: ignore[assignment]
    try:
        with TestClient(_probe_app()) as client:
            return client.request(method, path, json=body)
    finally:
        app_module._feedback = original


@pytest.mark.parametrize(("method", "path", "body"), MUTATIONS)
def test_a_refused_transition_is_a_422_with_its_own_code(
    method: str, path: str, body: dict[str, Any] | None
) -> None:
    """The whole point of the change: a rule is not a race.

    A viewer who presses ``Watchlist`` on a title they have already watched has
    hit a product rule, and no retry can change the answer. On a ``409`` a
    client cannot tell that from "somebody else committed first" without reading
    the sentence, so it is a ``422`` carrying a code instead.
    """
    detail = "a watched movie cannot be added to the watchlist"
    response = _call(InvalidStateTransitionError(detail), method, path, body)

    assert response.status_code == 422
    assert response.json() == {"detail": detail, "code": "transition_refused"}


@pytest.mark.parametrize(("method", "path", "body"), MUTATIONS)
def test_a_stale_revision_stays_a_409_and_names_itself(
    method: str, path: str, body: dict[str, Any] | None
) -> None:
    detail = "state revision 3 is stale; current revision is 5"
    response = _call(StateRevisionConflictError(detail), method, path, body)

    assert response.status_code == 409
    assert response.json() == {"detail": detail, "code": "revision_conflict"}


@pytest.mark.parametrize(("method", "path", "body"), MUTATIONS)
def test_a_reused_idempotency_key_is_a_409_of_its_own(
    method: str, path: str, body: dict[str, Any] | None
) -> None:
    """The second condition on the status, and the reason `code` exists at all.

    Both are races the write path recovers from identically, so splitting them
    is not what a client needs — but a status that means two things needs a
    field that means one.
    """
    detail = "idempotency key was already used for another mutation"
    response = _call(IdempotencyConflictError(detail), method, path, body)

    assert response.status_code == 409
    assert response.json() == {"detail": detail, "code": "idempotency_conflict"}


def test_a_validation_error_keeps_its_own_shape_on_the_same_status() -> None:
    """422 carries two bodies, and they have to stay distinguishable.

    FastAPI answers a malformed request on the same status, with ``detail`` as a
    list and no ``code``. A client that read the status alone would show a
    caller's own bug to a viewer as a product rule; reading ``code`` first is
    what keeps the two apart.
    """
    response = _call(
        InvalidStateTransitionError("unused"),
        "PUT",
        f"/users/{USER}/movies/{MOVIE}/rating",
        {"rating": 11.0},
    )

    assert response.status_code == 422
    body = response.json()
    assert "code" not in body
    assert isinstance(body["detail"], list)


def test_the_suite_covers_every_mutation_route_the_application_declares() -> None:
    """A route added without a case here would otherwise pass by not running."""
    declared = {
        (method, route.path)
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in route.methods
        if route.path in MUTATION_TEMPLATES and method in {"PUT", "DELETE"}
    }
    mounted = {
        (method, route.path)
        for route in _probe_app().routes
        if isinstance(route, APIRoute)
        for method in route.methods
        if method in {"PUT", "DELETE"}
    }

    assert declared == mounted
    assert len(declared) == len(MUTATIONS)


def test_every_refusal_the_service_raises_has_a_status_and_a_code() -> None:
    """No subclass may quietly inherit someone else's status.

    The mapping lives on the exception rather than at each call site precisely
    so a new one cannot be added without answering both questions.
    """
    subclasses = FeedbackMutationError.__subclasses__()

    assert {error.__name__ for error in subclasses} == {
        "StateRevisionConflictError",
        "IdempotencyConflictError",
        "InvalidStateTransitionError",
    }
    codes = {error.code for error in subclasses}
    assert codes == {"revision_conflict", "idempotency_conflict", "transition_refused"}
    assert len(codes) == len(subclasses), "two refusals sharing a code is not a contract"
    for error in subclasses:
        assert error.http_status in {409, 422}
